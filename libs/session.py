# -*- coding: utf-8 -*-
import os
import sys
import base64
import uuid
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

import json
import time 

from libs.api import API
from libs.utils import apiVersion

try:
    from Cryptodome.PublicKey import RSA
except ImportError:
    from Crypto.PublicKey import RSA

from jose import jwk

# When set, a login that would need the device to be registered (and so would
# put up the "how do you want to sign in" dialog) gives up silently instead.
# Background callers -- e.g. the IPTV Manager integration -- turn this on so
# they never interrupt the user with a dialog.
NO_PROMPT = False


class LoginError(Exception):
    """Signing in failed."""


class NotRegisteredError(LoginError):
    """The service does not know this device.

    /udid answered without a session: either this installation was never
    enrolled, or -- the case this exists for -- it was enrolled and has since
    been removed in the Vodafone TV administration, which invalidates every ks
    it was issued.
    """


def device_id_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'device_id.txt')


def device_id():
    """The vtv-id this installation is registered under.

    Kept in a file rather than in the settings: an action button launched from
    the settings dialog runs while the dialog still holds its own copy of the
    settings, and that copy is written back when the dialog closes -- so
    `setSetting` from such an action is silently undone. (That is exactly what
    happened to the first version of this: the new id was generated, saved, and
    then overwritten with the old one seconds later.)

    Generated on first use. A fresh id is unknown to the API, so the first
    login goes through /credentials, which registers it.
    """
    try:
        with open(device_id_path(), 'r', encoding='utf-8') as f:
            stored = f.read().strip()
        if stored:
            return stored
    except Exception:
        pass
    device = str(uuid.uuid4())
    store_device_id(device)
    xbmc.log('Vodafone TV > generated a device id: %s' % device, xbmc.LOGINFO)
    return device


def store_device_id(device):
    path = device_id_path()
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(device)
    return device


def new_device_id(device = None):
    """Set a fresh device id and drop the session that belonged to the old one.

    The new device is unknown to the API, so the next login falls back to
    /credentials, which registers it.
    """
    from libs.settings import Settings

    device = (device or str(uuid.uuid4())).strip()
    store_device_id(device)
    Settings().reset_json_data({'filename': 'session.txt', 'description': 'session'})
    xbmc.log('Vodafone TV > new device id %s (session cleared, the next login '
             'will register it)' % device, xbmc.LOGINFO)
    return device


# The key `CryptoUtils.encrypt` uses is published as a static file, so it can
# be picked up at runtime rather than trusting the copy shipped here.
PASSWORD_KEY_URL = 'https://3062.static-vfp2.ott.kaltura.com/3062/files/rsa_2048_pub.pem'
PASSWORD_KEY_TTL = 7 * 24 * 60 * 60


def bundled_password_key_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'password_key.pem')


def password_key_path():
    """Where a manually supplied key lives; it wins over everything else."""
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    override = os.path.join(profile, 'password_key.pem')
    if os.path.isfile(override):
        return override
    return bundled_password_key_path()


def cached_password_key_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'password_key_cache.pem')


def fetch_password_key(url = None):
    """Download the service's public key. Returns the PEM, or raises."""
    import ssl
    from urllib.request import urlopen, Request

    context = ssl._create_unverified_context()
    request = Request(url or PASSWORD_KEY_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=15, context=context) as response:
        pem = response.read().decode('utf-8', 'replace').strip()
    if 'BEGIN PUBLIC KEY' not in pem:
        raise ValueError('not a PEM public key')
    RSA.importKey(pem)  # refuse to cache something unusable
    return pem


def password_key():
    """The public key to encrypt the password with.

    A key placed in addon_data wins; otherwise the published one is fetched and
    cached for a week, and the copy shipped with the addon is the last resort
    so login still works offline or if the URL moves.
    """
    override = password_key_path()
    if override != bundled_password_key_path():
        with open(override, 'r', encoding='utf-8') as f:
            return f.read().strip()

    cache = cached_password_key_path()
    try:
        if os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < PASSWORD_KEY_TTL:
            with open(cache, 'r', encoding='utf-8') as f:
                cached = f.read().strip()
            if cached:
                return cached
    except Exception:
        pass

    try:
        from libs import dms
        pem = fetch_password_key(dms.password_key_url(PASSWORD_KEY_URL))
        directory = os.path.dirname(cache)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(cache, 'w', encoding='utf-8') as f:
            f.write(pem)
        xbmc.log('Vodafone TV > password key fetched from %s' % PASSWORD_KEY_URL,
                 xbmc.LOGINFO)
        return pem
    except Exception as e:
        xbmc.log('Vodafone TV > could not fetch the password key (%s), using the '
                 'bundled one' % e, xbmc.LOGWARNING)

    with open(bundled_password_key_path(), 'r', encoding='utf-8') as f:
        return f.read().strip()


def encrypt_password(password, public_key):
    """Encrypt a password the way the web player does.

    Its crypto helper is JSEncrypt -- `setPublicKey(key)` then `encrypt(text)`
    -- which is RSA with PKCS#1 v1.5 padding, base64 encoded. A 2048-bit key
    gives the 256 byte blob seen in login.har.
    """
    try:
        from Cryptodome.Cipher import PKCS1_v1_5
    except ImportError:
        from Crypto.Cipher import PKCS1_v1_5
    cipher = PKCS1_v1_5.new(RSA.importKey(public_key))
    return base64.b64encode(cipher.encrypt(password.encode('utf-8'))).decode('ascii')


# All three ways in are the same POST to the same base path with the same
# signed vtv-authentication header, only the body differs -- see the web
# player's bundle, where `SR_AUTH_BASE_PATH` is "authentication/v1" and the
# three paths are "udid", "credentials" and "pin".
AUTH_BASE = 'https://apigw.cz.vtv.vodafone.com/vtv/authentication/v1'

# What the TV app sends with a DAS challenge (Other_STV in its DeviceBrandId
# enum). The backend derives the real brand (32, Android) from the .wvd's
# client_id anyway; this is just the nominal value, matching the TV app.
DAS_BRAND_ID = '318'

# The brand the web player sends (Edge). Web auth (below) uses it.
WEB_BRAND_ID = '114'

# The service's RSA public modulus the web player encrypts its auth payload to
# (the JWE `n`); its exponent is the usual 65537. Lifted from the web bundle.
AUTH_SERVICE_KEY_MODULUS = ('0M79HosI1kc3fKXZaHvktV7Ccyk3m+l/fwxHUuhB6pdSKuyv6Up'
                            '8uCqfEolQclxsAPTW58K9SW6jSF4jOP+AA/a/wjGVs6wt1YwnPc'
                            '19ANwObWWVEi/kZRdPOo7blyhhieqkl03daWPqoQQRfYh+yRiOV'
                            'dfb58zsWK1Vq2kI51M=')


def auth_scheme():
    """Which login scheme is in force: 'web' (default) or 'das'.

    web -- identity is device_id() + an RSA keypair; the .wvd is only used for
           playback, so one .wvd can back several devices.
    das -- identity is the .wvd's Widevine device (Nagra DAS); one .wvd = one
           device, but sign-in is password-free and QR pairing is available.

    das is only allowed with a *custom* .wvd (the user's own). With just a
    vendored .wvd -- one shipped with the addon, which several users could
    share -- das would bind them all to the same Widevine identity, so it is
    forced to web regardless of the setting.
    """
    scheme = (xbmcaddon.Addon().getSetting('auth_scheme') or '').strip().lower()
    scheme = scheme if scheme in ('web', 'das') else 'web'
    if scheme == 'das':
        from libs import widevine
        if not widevine.has_custom_device():
            xbmc.log('Vodafone TV > das needs a custom .wvd; only a vendored '
                     'one is available, using web', xbmc.LOGINFO)
            return 'web'
    return scheme


def web_private_key():
    """This install's RSA keypair for the web auth scheme.

    Kept in profile/private.pem, generated on first use. Its public half is the
    devicePubKey the service encrypts the session right to, so it must persist
    across logins.
    """
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    path = os.path.join(profile, 'private.pem')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return RSA.import_key(f.read())
    key = RSA.generate(2048)
    if not os.path.isdir(profile):
        os.makedirs(profile)
    with open(path, 'wb') as f:
        f.write(key.export_key())
    xbmc.log('Vodafone TV > generated a web-auth RSA key', xbmc.LOGINFO)
    return key


def web_auth_header(private_key, device):
    """The `web:<jwt>` value that identifies this device to the service.

    A JWT (RS256, signed by our key) whose payload is a JWE (RSA-OAEP-256 to the
    service key) carrying our deviceId, model and public key -- exactly the web
    player's scheme.
    """
    from jose import jwt, jwe
    from jose.utils import long_to_base64

    service_key = {'alg': 'RS256', 'kty': 'RSA',
                   'n': AUTH_SERVICE_KEY_MODULUS,
                   'e': long_to_base64(65537)}
    payload = {
        'nonce': str(uuid.uuid4()),
        'deviceId': device,
        'model': 'Edge',
        'devicePubKey': {
            'kty': 'RSA',
            'n': long_to_base64(private_key.n).decode('ascii'),
            'e': long_to_base64(private_key.e).decode('ascii'),
        },
    }
    encrypted = jwe.encrypt(json.dumps(payload), service_key,
                            algorithm='RSA-OAEP-256', encryption='A128CBC-HS256')
    header = {'alg': 'RS256', 'typ': 'JWT',
              'kid': base64.urlsafe_b64encode(os.urandom(16)).decode('utf-8').rstrip('=')}
    return 'web:' + jwt.encode(encrypted, private_key, algorithm='RS256',
                               headers=header)


def web_session_key(private_key, encrypted_session_right):
    """The HMAC session key out of the web login's session right.

    The session right is a JWT whose payload is a JWE encrypted to our device
    key; decrypt it and read sessionKeys[0].keyValue -- the HS256 key
    Session.sign() needs.
    """
    from jose import jwt, jwe
    claims = jwt.decode(encrypted_session_right, private_key,
                        algorithms=['RS256'], options={'verify_signature': False})
    rights = json.loads(jwe.decrypt(claims, private_key).decode('utf-8'))
    return rights['sessionRight']['sessionKeys'][0]['keyValue']


def scheme_auth(api, scheme):
    """Auth material for one login/enroll/pair exchange, per scheme.

    Returns (headers, derive, brand):
      headers -- the vtv-* headers to add, identifying the device
      derive  -- called after the exchange populated api.session_key; returns
                 the HMAC session key out of the session right
      brand   -- the deviceBrandId to send in the body

    web identifies with a JWT over a generated deviceId + RSA key (the .wvd is
    not involved, so one .wvd can back many devices); das identifies with a
    Widevine DAS challenge from the .wvd itself. Both are accepted at /udid,
    /credentials and the pairing /pin poll -- the scheme just decides which
    identity a given enrollment registers.
    """
    if scheme == 'web':
        private_key = web_private_key()
        device = device_id()
        headers = {'vtv-authentication': web_auth_header(private_key, device),
                   'vtv-id': device}

        def derive():
            return web_session_key(
                private_key, api.session_key['encryptedSessionRight'])

        return headers, derive, WEB_BRAND_ID

    from libs import widevine
    cdm, auth = widevine.das_challenge()
    headers = {'vtv-authentication': auth}

    def derive():
        return widevine.das_session_key(
            cdm, api.session_key['encryptedSessionRight'])

    return headers, derive, DAS_BRAND_ID


def enroll_with_credentials(api, scheme):
    """Register this device into the household with an account name and password.

    Like the TV app's loginWithCredentials: POST {username, password,
    deviceBrandId} to /credentials with the scheme's auth header. Neither
    credential is stored -- this runs once, and afterwards /udid signs in on
    its own. The password goes out RSA-encrypted, the way the web player's
    CryptoUtils.encrypt (JSEncrypt, PKCS#1 v1.5) does it.

    Returns (data, session_key); (None, None) if the user backs out.
    """
    dialog = xbmcgui.Dialog()
    username = dialog.input('Přihlašovací jméno')
    if not username:
        return None, None
    password = dialog.input('Heslo', type = xbmcgui.INPUT_ALPHANUM,
                            option = xbmcgui.ALPHANUM_HIDE_INPUT)
    if not password:
        return None, None

    auth_headers, derive, brand = scheme_auth(api, scheme)
    headers = api.headers.copy()
    headers.update(auth_headers)
    post = {'username': username.strip(),
            'password': encrypt_password(password, password_key()),
            'deviceBrandId': brand}
    data = api.call_api(url = AUTH_BASE + '/credentials', data = post,
                        headers = headers, sensitive = True)
    if not find_ks(data):
        return data, None
    return data, derive()


def register_device(api, scheme):
    """Enroll this device, the way the TV app does at first start.

    Offers both ways in -- QR pairing or username/password -- for either
    scheme; the registered identity follows `scheme`. Returns
    (data, session_key); (None, None) if the user backs out.
    """
    from libs import pairing

    if NO_PROMPT:
        xbmc.log('Vodafone TV > device not enrolled and running without a UI '
                 '(NO_PROMPT); skipping enrollment', xbmc.LOGINFO)
        return None, None

    choice = xbmcgui.Dialog().select(
        'Zařízení není přihlášené',
        ['Naskenovat QR kód mobilní aplikací',
         'Přihlásit jménem a heslem'])
    if choice == 0:
        return pairing.pair(api, scheme = scheme)
    if choice == 1:
        return enroll_with_credentials(api, scheme)
    return None, None


def profile_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'profile_id.txt')


def selected_profile():
    """The user id the addon should switch to, or None for the default.

    In a file rather than a setting for the same reason as the device id: a
    settings-dialog action button cannot write settings reliably.
    """
    try:
        with open(profile_path(), 'r', encoding='utf-8') as f:
            return f.read().strip() or None
    except Exception:
        return None


def store_profile(user_id):
    path = profile_path()
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(str(user_id))
    xbmc.log('Vodafone TV > profile set to %s' % user_id, xbmc.LOGINFO)
    return str(user_id)


def find_ks(data):
    """The ks out of a login response, whatever it wraps it in.

    /credentials answers with result.loginSession.ks; the shape /udid uses is
    not documented anywhere here, so look for a loginSession first and then for
    any plausible ks.
    """
    if isinstance(data, dict):
        session = data.get('loginSession')
        if isinstance(session, dict) and session.get('ks'):
            return session['ks']
        ks = data.get('ks')
        if isinstance(ks, str) and ks:
            return ks
        for value in data.values():
            found = find_ks(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_ks(item)
            if found:
                return found
    return None


def find_expiry(data):
    """The login session's expiry, so a skipped switchUser still has one."""
    if isinstance(data, dict):
        session = data.get('loginSession')
        if isinstance(session, dict) and session.get('expiry'):
            return session['expiry']
        expiry = data.get('expiry')
        if isinstance(expiry, int) and expiry:
            return expiry
        for value in data.values():
            found = find_expiry(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_expiry(item)
            if found:
                return found
    return None


def same_user_error(data):
    """True when switchUser refused because we are already that user."""
    text = str(login_error(data) or data or '').lower()
    return 'same user' in text


def login_error(data):
    """A short description of why a login response carried no session."""
    if not isinstance(data, dict):
        return None
    if 'err' in data:
        return '%s %s' % (data.get('err'), str(data.get('body'))[:120])
    result = data.get('result')
    if isinstance(result, dict) and isinstance(result.get('error'), dict):
        error = result['error']
        return '%s %s' % (error.get('code', ''), error.get('message', ''))
    return None


# Once signing in again has failed, stop trying for a while: every further
# request would fail the same way, and the user has already been told once. A
# plugin run is over long before this lapses; the service, which lives as long
# as Kodi does, gets to notice a device that was registered again in the
# meantime.
_renew_failed_at = 0
RENEW_RETRY_AFTER = 10 * 60

UNREGISTERED_MESSAGE = (
    'Zařízení už není u Vodafone TV přihlášené.\n'
    'Nejspíš bylo odebráno ve správě účtu (Moje zařízení), '
    'nebo jeho registrace vypršela.\n\n'
    'Chcete zařízení přihlásit znovu?')

# The same thing in one line, for a notification (where a dialog is not an
# option -- during playback, or after the user declined the dialog).
UNREGISTERED_NOTIFICATION = ('Zařízení není přihlášené – přihlaste ho prosím '
                             'znovu (Nastavení / QR kód)')


def ask_reregister():
    """Say the device is not signed in any more, and offer to fix it now."""
    return bool(xbmcgui.Dialog().yesno('Vodafone TV', UNREGISTERED_MESSAGE,
                                       nolabel = 'Zrušit',
                                       yeslabel = 'Přihlásit znovu'))


def recover_expired(data, session = None, prompt = True):
    """Sign in again when `data` was refused because our ks is stale.

    Returns the signed-in Session when the caller should repeat its request --
    with the fresh `session.ks`, the old one stays refused -- and None when the
    response was not a session failure or signing in again did not work out.
    """
    from libs.api import is_ks_error

    if not is_ks_error(data):
        return None
    if time.time() - _renew_failed_at < RENEW_RETRY_AFTER:
        return None
    xbmc.log('Vodafone TV > the API refused our session (KS expired); signing '
             'in again', xbmc.LOGWARNING)
    session = session or Session()
    return session if session.renew(prompt = prompt) else None


class Session:
    def __init__(self):
        self.load_session()

    def create_session(self):
        self.get_token()
        self.save_session()

    def get_token(self, enroll = True):
        # Sign in with the configured scheme. Both set self.session_key and hand
        # back (ks, login_expiry); after that the flow is identical -- find the
        # profile with household/get, then switchUser to it.
        api = API()
        ks, login_expiry = self._login(api, auth_scheme(), enroll = enroll)

        headers = api.headers
        headers.pop('vtv-authentication', None)
        headers.pop('vtv-id', None)

        post = {'apiVersion' : apiVersion, 'ks' : ks}
        req_body = self.sign(headers, post)

        data = api.call_api(url = 'https://apigw.cz.vtv.vodafone.com/vtv/phoenix/v1/api_v3/service/household/action/get', data = req_body, headers = api.headers)
        if 'err' in data or not 'result' in data or not 'masterUsers' in data['result'] or len(data['result']['masterUsers']) == 0:
            xbmcgui.Dialog().notification('Vodafone TV','Problém při přihlášení1', xbmcgui.NOTIFICATION_ERROR, 5000)
            sys.exit() 
        # The TV API gateway (search) wants these as DomainID / SiteGuid
        self.household_id = data['result'].get('id')
        master_userid = data['result']['masterUsers'][0]['id']
        self.household_users = [u['id'] for u in data['result'].get('users', [])]

        # Whichever profile was picked in the menu, as long as it is still in
        # the household; otherwise the first non-master user, as before.
        userid = None
        chosen = selected_profile()
        if chosen and int(chosen) in [int(u) for u in self.household_users]:
            userid = int(chosen)
        else:
            for user in data['result']['users']:
                if user['id'] != master_userid:
                    userid = user['id']
        self.user_id = userid
        if userid is None:
            xbmcgui.Dialog().notification('Vodafone TV','Problém při přihlášení2', xbmcgui.NOTIFICATION_ERROR, 5000)
            sys.exit()
        xbmc.log('Vodafone TV > switching to profile %s (household users: %s)'
                 % (userid, ', '.join(str(u) for u in self.household_users)), xbmc.LOGINFO)

        post = {'ks' : ks, 'apiVersion' : apiVersion, 'userIdToSwitch' : userid}

        req_body = self.sign(headers, post)

        data = api.call_api(url = 'https://apigw.cz.vtv.vodafone.com/vtv/phoenix/v1/api_v3/service/session/action/switchUser', data = post, headers = api.headers)
        if 'err' in data or not 'result' in data or not 'objectType' in data['result'] or data['result']['objectType'] != 'KalturaLoginSession':
            # The login already returns a session for the master profile, so
            # switching to that one is refused with "Cannot change to same
            # user" -- which means we are on it already and the ks we have is
            # the right one.
            if same_user_error(data):
                xbmc.log('Vodafone TV > already signed in as %s, keeping the '
                         'login session' % userid, xbmc.LOGINFO)
                self.ks = ks
                self.ks_expiry = login_expiry or (int(time.time()) + 6 * 60 * 60)
                return

            xbmcgui.Dialog().notification('Vodafone TV','Problém při přihlášení3', xbmcgui.NOTIFICATION_ERROR, 5000)
            sys.exit()
        self.ks = data['result']['ks']
        self.ks_expiry = data['result']['expiry']

    def _login(self, api, scheme, enroll = True):
        """Sign in with the given scheme; set self.session_key.

        /udid signs the device in when it is already registered (no password).
        A device the service does not know yet is registered once -- by QR
        pairing or username/password -- exactly like the TV app at first start.
        The session right is decoded per scheme (a Widevine licence for das, a
        JWE to our RSA key for web). Returns (ks, login_expiry).

        With `enroll` off, a device the service does not know raises
        NotRegisteredError instead of putting up the enrollment dialog -- that
        is what renew() wants: sign a registered device back in silently, and
        tell the difference between an expired session and a device that was
        unregistered.
        """
        auth_headers, derive, brand = scheme_auth(api, scheme)
        headers = api.headers
        headers.update(auth_headers)

        data = api.call_api(url = AUTH_BASE + '/udid',
                            data = {'deviceBrandId': brand},
                            headers = headers)
        ks = find_ks(data)
        if ks:
            self.session_key = derive()
            xbmc.log('Vodafone TV > logged in with the device (%s)' % scheme,
                     xbmc.LOGINFO)
            return ks, find_expiry(data)

        if enroll == False:
            # Nothing to enroll here -- but keep a request that never reached
            # the service (network trouble) apart from a real "who are you?".
            if isinstance(data, dict) and 'err' in data:
                raise LoginError(login_error(data))
            raise NotRegisteredError(login_error(data) or 'no ks in the response')

        xbmc.log('Vodafone TV > device not registered (%s); asking how to '
                 'enroll it' % (login_error(data) or 'no ks in the response'),
                 xbmc.LOGINFO)
        data, session_key = register_device(api, scheme)
        if data is None:
            xbmc.log('Vodafone TV > enrollment cancelled', xbmc.LOGINFO)
            sys.exit()
        ks = find_ks(data)
        if not ks:
            errmsg = login_error(data)
            xbmcgui.Dialog().notification(
                'Vodafone TV',
                'Přihlášení se nezdařilo' + (' - ' + errmsg if errmsg else ''),
                xbmcgui.NOTIFICATION_ERROR, 6000)
            sys.exit()
        self.session_key = session_key
        xbmc.log('Vodafone TV > device enrolled and signed in (%s)' % scheme,
                 xbmc.LOGINFO)
        return ks, find_expiry(data)

    def renew(self, prompt = True):
        """Sign in again after the service refused the ks we hold.

        The everyday case -- the session simply expired -- is invisible: /udid
        signs the registered device straight back in, no password and no
        dialog. A device the service no longer knows cannot be recovered that
        way, so the user is told what happened and offered the enrollment
        dialog rather than being left with "something went wrong".

        Returns True when a fresh ks is in place; the caller must then repeat
        its request with the new self.ks.

        The stored session is left alone until a new one replaces it: clearing
        it up front would send every Session() built later in this run into the
        enrollment dialog, one per listing.
        """
        global _renew_failed_at

        try:
            self.get_token(enroll = False)
        except NotRegisteredError as e:
            xbmc.log('Vodafone TV > the service does not know this device any '
                     'more (%s) -- it was probably unregistered in the Vodafone '
                     'TV administration' % e, xbmc.LOGWARNING)
            if prompt == False or NO_PROMPT or not ask_reregister():
                _renew_failed_at = time.time()
                return False
            self.get_token()  # the full flow, enrollment dialog and all
        except LoginError as e:
            xbmc.log('Vodafone TV > signing in again failed: %s' % e, xbmc.LOGWARNING)
            _renew_failed_at = time.time()
            return False

        self.save_session()
        xbmc.log('Vodafone TV > signed in again after the API refused our session',
                 xbmc.LOGINFO)
        return True

    def sign(self, headers, post):
        sign_key_data = dict()
        sign_key_data['alg'] = 'HS256'
        sign_key_data['kty'] = 'oct'
        sign_key_data['k'] = self.session_key
        req_body = json.dumps(post, separators=(',', ':')).encode("utf-8")

        sign_key = jwk.construct(sign_key_data, "HS256")
        signature = sign_key.sign(req_body)

        headers.update({'vtv-authorization' : f"HMAC-SHA256:{signature.hex()}"})
        return req_body

    def load_session(self):
        from libs.settings import Settings
        settings = Settings()
        data = settings.load_json_data({'filename' : 'session.txt', 'description' : 'session'})
        if data is not None :
            data = json.loads(data)
            if 'ks_expiry' not in data or int(data['ks_expiry']) < int(time.time()):
                self.create_session()
            else:
                self.session_key = data['session_key']
                self.ks = data['ks']
                self.ks_expiry = data['ks_expiry']
                self.household_id = data.get('household_id')
                self.user_id = data.get('user_id')
        else:
            self.create_session()
        self.save_session

    def save_session(self):
        from libs.settings import Settings
        settings = Settings()
        data = json.dumps({'ks' : self.ks, 'ks_expiry' : self.ks_expiry, 'session_key' : self.session_key,
                           'household_id' : getattr(self, 'household_id', None),
                           'user_id' : getattr(self, 'user_id', None)})
        settings.save_json_data({'filename' : 'session.txt', 'description' : 'session'}, data)

    def get_household(self):
        """(DomainID, SiteGuid) for the TV API gateway.

        Saved at login; an older session.txt predates that, so fetch them once
        rather than forcing a full re-login.
        """
        if getattr(self, 'household_id', None) and getattr(self, 'user_id', None):
            return self.household_id, self.user_id

        from libs.api import API
        api = API()
        headers = api.headers.copy()
        req_body = self.sign(headers, {'apiVersion': apiVersion, 'ks': self.ks})
        data = api.call_api(url = 'https://apigw.cz.vtv.vodafone.com/vtv/phoenix/v1/api_v3/service/household/action/get',
                            data = req_body, headers = headers)
        result = data.get('result') if isinstance(data, dict) else None
        if not result or 'masterUsers' not in result:
            raise RuntimeError('household/get failed: %s' % (login_error(data) or data))

        self.household_id = result.get('id')
        master = result['masterUsers'][0]['id']
        self.user_id = next((u['id'] for u in result.get('users', []) if u['id'] != master), master)
        self.save_session()
        xbmc.log('Vodafone TV > household %s / user %s' % (self.household_id, self.user_id),
                 xbmc.LOGINFO)
        return self.household_id, self.user_id

    def remove_session(self):
        from libs.settings import Settings
        settings = Settings()
        settings.reset_json_data({'filename' : 'session.txt', 'description' : 'session'})
        self.create_session()
        xbmcgui.Dialog().notification('Vodafone TV', 'Byla vytvořená nová session', xbmcgui.NOTIFICATION_INFO, 5000)

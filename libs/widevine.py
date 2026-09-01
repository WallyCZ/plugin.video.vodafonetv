# -*- coding: utf-8 -*-
"""Local Widevine CDM support for Vodafone TV.

InputStream Adaptive never activates Widevine privacy mode (see
xbmc/inputstream.adaptive#1850), so its challenge carries a plaintext
client_id and the Nagra SSP license server rejects it. This module does the
license exchange itself using a .wvd device file: it builds a privacy-mode
challenge with libs.pywidevine, posts it to the Vodafone ccursession API,
decrypts the content keys and hands them to ISA as ClearKey keyids.
"""
import os
import ssl
import time
import base64
import json

import xbmc
import xbmcaddon
import xbmcvfs

from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from libs.api import find_api_error
from libs.pywidevine import Cdm, Device, PSSH
from libs.pywidevine.cdm import (SERVICE_CERTIFICATE_CHALLENGE, ServiceCertificate,
                                 make_core_message, LicenseType)
from libs.pywidevine.device import find_wvd
from libs.utils import apiVersion

WIDEVINE_DRM_ID = 'edef8ba9-79d6-4ace-a3c8-27dcd51d21ed'
WIDEVINE_SCHEME_ID = 'urn:uuid:' + WIDEVINE_DRM_ID

CENC_NS = 'urn:mpeg:cenc:2013'
MPD_NS = 'urn:mpeg:dash:schema:mpd:2011'

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0')


def log(message, level=xbmc.LOGINFO):
    xbmc.log('Vodafone TV WV > ' + message, level)


class WidevineError(Exception):
    """A DRM failure. `code` is the API's error code when there was one."""

    def __init__(self, message, code=None):
        Exception.__init__(self, message)
        self.code = code


# What to put in front of the user for a given API error code. Everything else
# gets a generic message with the technical detail left to the log.
FRIENDLY_ERRORS = {
    # Observed on channels the household has no subscription for (HBO HD).
    # The gateway reports it as an internal error rather than an entitlement
    # one, so the wording stays careful.
    'apigw-50000': 'Kanál se nepodařilo odemknout – pravděpodobně není '
                   'součástí vašeho předplatného',
    # One playback session at a time; a previous one is still open.
    '1007': 'Přehrávání blokuje jiná relace. Zkuste to prosím za chvíli znovu',
    '2001': 'Licenční server odmítl požadavek',
    'apigw-11001': 'Neplatný požadavek na API',
    # wvd-vault has no free .wvd to lease right now.
    'vault_no_device': 'Není volné zařízení. Zkuste to prosím za pár dní.',
    # wvd-vault did not accept the proof that we hold a Vodafone session.
    'vault_rejected': 'Server se zařízeními neověřil vaše předplatné – '
                      'zkuste vytvořit novou session',
    # The household already uses as many devices as the vault hands out.
    'vault_household_limit': 'Vaše domácnost už využívá všechna povolená '
                             'zařízení – uvolněte jedno z nich',
    # The ks we signed the license request with is not accepted any more. By
    # this point the manifest call has already tried to sign in again, so what
    # is left is a device the service no longer knows.
    '500015': 'Zařízení není přihlášené – přihlaste ho prosím znovu',
    '500016': 'Zařízení není přihlášené – přihlaste ho prosím znovu',
}


def friendly_error(error):
    """A short Czech message for a notification; details stay in the log."""
    code = getattr(error, 'code', None)
    if code and code in FRIENDLY_ERRORS:
        return FRIENDLY_ERRORS[code]

    text = str(error)
    if 'vault unreachable' in text:
        return 'Server se zařízeními neodpovídá – zkontrolujte adresu v nastavení'
    if 'vault 401' in text:
        return 'Server se zařízeními odmítl klíč API – zkontrolujte nastavení'
    if 'not signed in' in text:
        return 'Zařízení lze vyžádat až po přihlášení k Vodafone TV'
    if '.wvd' in text:
        return 'Chybí soubor .wvd – zkontrolujte nastavení DRM'
    if 'no Widevine' in text:
        return 'Kanál používá DRM, které doplněk neumí (není Widevine)'
    if isinstance(code, int) and 500 <= code < 600:
        # The CDN, not the API -- e.g. tivio answers 500 for a channel it has
        # no stream for, whatever the request looks like.
        return 'Server kanálu neodpovídá (HTTP %s), zkuste to později' % code
    if code:
        return 'Přehrávání selhalo (chyba %s)' % code
    return 'Přehrávání selhalo – podrobnosti jsou v logu'


def redact(value, limit=48):
    """JSON dump with the session key and challenge shortened, for logging."""
    def shorten(o):
        if isinstance(o, dict):
            return dict((k, shorten(v)) for k, v in o.items())
        if isinstance(o, list):
            return [shorten(v) for v in o]
        if isinstance(o, str) and len(o) > limit:
            return '%s...<%d chars>' % (o[:limit], len(o))
        return o
    return json.dumps(shorten(value))


# ---------------------------------------------------------------------------
# device file
# ---------------------------------------------------------------------------

def is_enabled():
    return xbmcaddon.Addon().getSetting('use_pywidevine') == 'true'


def isa_fallback_enabled():
    """Whether a failed local CDM should hand the stream to ISA's Widevine.

    Off by default: ISA cannot do privacy mode, so it cannot succeed here, and
    each attempt costs 7-8 license retries -- every one of them a bookSlot plus
    a start, which occupies the account's session slot and makes the next real
    attempt fail too.
    """
    return xbmcaddon.Addon().getSetting('wv_isa_fallback') == 'true'



def get_device_path():
    """Configured .wvd, else the first one found in addon_data or the addon."""
    addon = xbmcaddon.Addon()
    configured = addon.getSetting('wvd_file')
    if configured and os.path.isfile(configured):
        return configured

    profile_dir = xbmcvfs.translatePath(addon.getAddonInfo('profile'))
    addon_dir = xbmcvfs.translatePath(addon.getAddonInfo('path'))
    return find_wvd(profile_dir, addon_dir, os.path.join(addon_dir, 'libs'))


def load_device(session=None):
    path = get_device_path()
    if not path:
        # No local .wvd: lease one from the wvd-vault, if configured. It is then
        # cached in the profile dir and found by get_device_path() from now on.
        # The vault wants proof that we hold a live Vodafone session, so it is
        # handed the one we are playing with (loaded from disk when there is
        # none, e.g. when a caller reaches this outside playback).
        from libs import vault
        if vault.is_configured():
            try:
                path = vault.fetch(session)
            except vault.VaultNoDevice:
                raise WidevineError('no free device available from the vault -- '
                                    'try again in a few days',
                                    code='vault_no_device')
            except vault.VaultHouseholdLimit as e:
                raise WidevineError('the household already holds every device '
                                    'the vault allows it (%s)' % e,
                                    code='vault_household_limit')
            except vault.VaultRejected as e:
                raise WidevineError('the vault refused our proof of '
                                    'subscription: %s' % e,
                                    code='vault_rejected')
            except vault.VaultError as e:
                raise WidevineError('vault error: %s' % e)
    if not path:
        raise WidevineError(
            'no .wvd device file found -- copy one into the addon data folder '
            '(%s), set it in the addon settings, or configure a wvd-vault URL'
            % xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile')))
    device = Device.load(path)
    log('using device %s (%s)' % (os.path.basename(path), device.describe()))
    return device


def has_custom_device():
    """True when the .wvd in use is the user's own, not one shipped here.

    A custom .wvd is pointed at by the wvd_file setting or dropped into
    addon_data; a vendored one lives inside the addon's install directory. DAS
    binds the household to the .wvd's Widevine identity, so it is only sound
    with a device that is uniquely the user's -- were several users to share one
    vendored .wvd, DAS would map them all to the same device. Web auth never
    uses the .wvd for identity, so it is fine with a vendored one.
    """
    path = get_device_path()
    if not path:
        return False
    addon_dir = os.path.abspath(
        xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('path')))
    resolved = os.path.abspath(path)
    return not (resolved == addon_dir or resolved.startswith(addon_dir + os.sep))


# ---------------------------------------------------------------------------
# DAS device attestation (the `das` login scheme)
# ---------------------------------------------------------------------------
#
# The service authenticates a device with a Nagra DAS challenge sent as the
# `vtv-authentication: widevine:<base64>` header. Decompiling the TV app's DAS
# SDK (x3/d.java, DasImpl.e()) shows this is a plain Widevine getKeyRequest:
#
#     init_data = base64("CAESEKqqqqqqqqqqqqqqqqqqqqo=")   # PSSH, key_id = 0xAA*16
#     getKeyRequest(session, init_data, "video/avc", STREAMING, {deviceUniqueId})
#     header = "widevine:" + base64(challenge)
#
# with no service certificate (client_id in the clear). Our CDM produces the
# same message, and the DAS backend accepts our .wvd. The login response's
# `encryptedSessionRight` is then a Widevine LICENSE whose OPERATOR_SESSION key
# is the HMAC key the gateway wants -- parse_license recovers it.

# key_id = 0xAA*16, exactly what the TV app's DasImpl.d() hands to MediaDrm
DAS_INIT_DATA = bytes.fromhex('08011210' + 'aa' * 16)


def das_challenge():
    """(cdm, header) for a fresh DAS attestation.

    Keep the returned cdm: das_session_key() needs the same instance to derive
    the session key from the license the server sends back.
    """
    cdm = Cdm(load_device())
    challenge = cdm.get_license_challenge(DAS_INIT_DATA, LicenseType.STREAMING,
                                          privacy_mode=False)
    return cdm, 'widevine:' + base64.b64encode(challenge).decode('ascii')


def das_session_key(cdm, encrypted_session_right):
    """The HMAC key for signed requests, out of the DAS login's session right.

    `encrypted_session_right` is the base64 Widevine LICENSE from the login
    response's `vtv-sessionkey` header. Returns it base64url-encoded, ready to
    drop into Session.session_key (which sign() feeds to a JWK as `k`).
    """
    keys = cdm.parse_license(encrypted_session_right)
    session_keys = [k for k in keys if k.type == 'OPERATOR_SESSION'] or keys
    if not session_keys:
        raise WidevineError('DAS session right carried no key')
    raw = bytes.fromhex(session_keys[0].key)
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def fetch_pssh(manifest_url):
    """Return the Widevine PSSH objects advertised by a DASH manifest."""
    context = ssl._create_unverified_context()
    request = Request(url=manifest_url, headers={'User-Agent': USER_AGENT})
    with urlopen(request, timeout=15, context=context) as response:
        manifest = response.read()
    return parse_pssh(manifest)


def parse_pssh(manifest):
    root = ET.fromstring(manifest)

    boxes = []
    default_kids = []
    schemes = []
    for node in root.iter('{%s}ContentProtection' % MPD_NS):
        scheme = (node.get('schemeIdUri') or '').lower()
        schemes.append(scheme)
        kid = node.get('{%s}default_KID' % CENC_NS)
        if kid:
            default_kids.append(kid)
        if scheme != WIDEVINE_SCHEME_ID:
            continue
        pssh_node = node.find('{%s}pssh' % CENC_NS)
        if pssh_node is not None and pssh_node.text:
            boxes.append(pssh_node.text.strip())

    result = []
    seen = set()
    for box in boxes:
        try:
            pssh = PSSH.from_b64(box)
        except Exception as e:
            log('ignoring unusable pssh in manifest: %s' % e, xbmc.LOGWARNING)
            continue
        if pssh.init_data not in seen:
            seen.add(pssh.init_data)
            result.append(pssh)

    # Manifests that only carry default_KID still let us build init_data.
    if not result:
        for kid in default_kids:
            try:
                pssh = PSSH.from_key_id(kid)
            except Exception as e:
                log('ignoring unusable default_KID %s: %s' % (kid, e), xbmc.LOGWARNING)
                continue
            if pssh.init_data not in seen:
                seen.add(pssh.init_data)
                result.append(pssh)

    if not result:
        if schemes:
            # Encrypted, just not with anything we can license. Say so rather
            # than pretending it is a clear stream and failing inside ISA.
            raise WidevineError('stream is encrypted but carries no Widevine '
                                'protection (schemes: %s)' % ', '.join(sorted(set(schemes))))
        # Some channels (e.g. DVTV Extra) are simply not encrypted.
        log('manifest carries no ContentProtection -- unencrypted stream')
        return []
    log('manifest carries %d Widevine pssh: %s'
        % (len(result), ', '.join(repr(p) for p in result)))
    return result


# ---------------------------------------------------------------------------
# license exchange
# ---------------------------------------------------------------------------

def bookmark_action(session, asset_id, program_id, file_id):
    # VOD carries no programId: it is a media asset in its own right, not a
    # programme on a channel. The web player's VOD bookmark omits programId (and
    # uses FIRST_PLAY); live/archive send the programme as programId. Build the
    # bookmark either way and only add programId when there is one.
    bookmark = {
        'objectType': 'KalturaBookmark',
        'id': str(asset_id),
        'type': 'media',
        'position': 0,
        'playerData': {
            'objectType': 'KalturaBookmarkPlayerData',
            'fileId': int(file_id),
            'action': 'PLAY',
        },
    }
    # '' / 'None' can arrive from a stringified None (see take_playback); treat
    # them the same as a real None -- a VOD bookmark carries no programId.
    if program_id not in (None, '', 'None'):
        bookmark['programId'] = int(program_id)
    return {
        'action': 'bookmark',
        'params': {},
        'body': {
            'bookmark': bookmark,
            'apiVersion': apiVersion,
            'ks': session.ks,
        },
    }


SESSION_ACTION = {'action': 'session', 'params': {}, 'body': {}}


def make_book_slot_request(session, asset_id, program_id, file_id):
    """The ccursession/bookSlot batch, same as stream.start_license_session."""
    return {'requests': [bookmark_action(session, asset_id, program_id, file_id),
                         SESSION_ACTION],
            'apiVersion': apiVersion, 'ks': session.ks}


def make_license_request(session, entitlement_body, asset_id, program_id,
                         file_id, challenge_b64):
    """The ccursession/start batch: entitlement, bookmark, session, license.

    All four actions are mandatory -- the API gateway answers a shorter batch
    with apigw-11001 "Invalid request. Missing mandatory fields."
    """
    license_action = {
        'action': 'license',
        'params': {
            'drmId': WIDEVINE_DRM_ID,
            'challenge': challenge_b64,
        },
        'body': {},
    }

    requests = [
        {
            'action': 'checkEntitlement',
            'params': {},
            'body': entitlement_body,
        },
        bookmark_action(session, asset_id, program_id, file_id),
        SESSION_ACTION,
        license_action,
    ]
    return {'requests': requests, 'apiVersion': apiVersion, 'ks': session.ks}


PLAYBACK_PROPERTY = 'vodafonetv.playing'


def remember_playback(asset_id, program_id, file_id):
    """Record what is playing so the service can tear the session down."""
    import xbmcgui
    xbmcgui.Window(10000).setProperty(
        PLAYBACK_PROPERTY, '%s|%s|%s' % (asset_id, program_id, file_id))


def take_playback():
    """Consume the recorded playback ids, or None if nothing is recorded."""
    import xbmcgui
    window = xbmcgui.Window(10000)
    value = window.getProperty(PLAYBACK_PROPERTY)
    if not value:
        return None
    window.clearProperty(PLAYBACK_PROPERTY)
    parts = value.split('|')
    if len(parts) != 3:
        return None
    # program_id is None for VOD; it round-trips through the window property as
    # the string 'None', so map it back so teardown does not try int('None').
    asset_id, program_id, file_id = parts
    if program_id in ('', 'None'):
        program_id = None
    return [asset_id, program_id, file_id]


def session_token_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'wv_session_token.txt')


def store_session_token(token):
    """Keep the token a start handed us; teardown cannot release without it."""
    try:
        directory = os.path.dirname(session_token_path())
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(session_token_path(), 'w', encoding='utf-8') as f:
            f.write(token or '')
    except Exception as e:
        log('could not store the session token: %s' % e, xbmc.LOGWARNING)


def take_session_token():
    """Read and clear the stored token -- a token is good for one teardown."""
    try:
        with open(session_token_path(), 'r', encoding='utf-8') as f:
            token = f.read().strip()
        os.remove(session_token_path())
        return token or None
    except Exception:
        return None


def capture_session_token(data):
    """Store any sessionToken found in a start response. True if one was stored.

    `data` is the parsed start response -- a 200 body or the JSON parsed out of
    an HTTPError body. A start holds a session as soon as its `session` action
    succeeds, even when the `license` action in the same batch is refused, and
    the held session's token comes back regardless. Capturing it here, before
    the caller raises on the license error, is what keeps a failed start from
    leaking a session with no way to release it (the cause of the permanent
    NagraSSMException 1007 deadlock).
    """
    token = find_session_token(data)
    if token:
        store_session_token(token)
        log('session token stored (%d chars) for teardown' % len(token))
        return True
    return False


def parse_error_body(body):
    """Parse an HTTPError body (a JSON string) into a dict, or None."""
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def find_session_token(data):
    """The start response carries the sessionToken teardown needs."""
    if isinstance(data, dict):
        value = data.get('sessionToken')
        if isinstance(value, str) and value:
            return value
        for value in data.values():
            found = find_session_token(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_session_token(item)
            if found:
                return found
    return None


def make_teardown_request(session, asset_id, program_id, file_id, token,
                          position=0):
    """Exactly what the web player sends when playback stops.

    Captured from the browser: the bookmark says STOP rather than PLAY, and the
    session action carries the sessionToken the start response returned. A
    teardown without that token releases nothing -- which is why our earlier
    attempts were accepted with an empty body and changed nothing.
    """
    bookmark = bookmark_action(session, asset_id, program_id, file_id)
    bookmark['body']['bookmark']['position'] = position
    bookmark['body']['bookmark']['playerData']['action'] = 'STOP'
    return {
        'requests': [
            bookmark,
            {'action': 'session', 'params': {'sessionToken': token}, 'body': {}},
        ],
        'apiVersion': apiVersion,
        'ks': session.ks,
    }


def teardown(session, api, asset_id, program_id, file_id, token=None,
             position=0):
    """Release the playback session. Never fatal."""
    if token is None:
        token = take_session_token()
    if not token:
        log('no session token stored -- nothing to tear down')
        return False

    post = make_teardown_request(session, asset_id, program_id, file_id, token,
                                 position)
    try:
        headers = api.headers.copy()
        body = session.sign(headers, post)
        data = api.call_api(
            url='https://apigw.cz.vtv.vodafone.com/vtv/ccursession/v1/teardown',
            data=body, headers=headers)
        if isinstance(data, dict) and 'err' in data:
            log('teardown failed: %s %s'
                % (data.get('err'), _one_line(data.get('body'))), xbmc.LOGWARNING)
            return False
        error = find_api_error(data)
        if error:
            log('teardown refused: %s' % describe_api_error(error), xbmc.LOGWARNING)
            return False
        log('teardown ok (session released)')
        return True
    except Exception as e:
        log('teardown errored: %s' % e, xbmc.LOGWARNING)
        return False


def book_slot(session, api, asset_id, program_id, file_id):
    """Book the playback slot that a following /start consumes.

    One bookSlot permits exactly one start: a start without a fresh slot is
    answered `NagraSSMException 1007 Bad request`, which is easy to mistake for
    a rejected challenge.
    """
    post = make_book_slot_request(session, asset_id, program_id, file_id)
    headers = api.headers.copy()
    body = session.sign(headers, post)
    data = api.call_api(
        url='https://apigw.cz.vtv.vodafone.com/vtv/ccursession/v1/bookSlot',
        data=body, headers=headers)
    if isinstance(data, dict) and 'err' in data:
        error = error_in_body(data.get('body'))
        raise WidevineError('bookSlot failed: %s %s'
                            % (data.get('err'), _one_line(data.get('body'))),
                            code=error.get('code') if error else None)
    error = find_api_error(data)
    if error:
        raise WidevineError('bookSlot refused: %s' % describe_api_error(error),
                            code=error.get('code'))
    log('bookSlot ok: %s' % redact(data))
    return data


def describe_api_error(error):
    return '%s %s: %s' % (error.get('objectType', ''), error.get('code', ''),
                          error.get('message', ''))


def error_in_body(body):
    """Pull the API error out of an HTTPError body (a JSON string)."""
    if not body:
        return None
    try:
        return find_api_error(json.loads(body))
    except Exception:
        return None


def find_license(data):
    """Pull the base64 license out of whatever shape the API answers with."""
    if isinstance(data, dict):
        value = data.get('license')
        if isinstance(value, str) and value:
            return value
        for value in data.values():
            found = find_license(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_license(item)
            if found:
                return found
    return None


def request_license(session, api, entitlement_body, asset_id, program_id,
                    file_id, challenge, book=True):
    if book:
        # Release a session an earlier playback left behind -- only when we
        # actually hold its token, so this is a no-op rather than a blind call
        # against whatever state the server is in.
        token = take_session_token()
        if token:
            teardown(session, api, asset_id, program_id, file_id, token)
        book_slot(session, api, asset_id, program_id, file_id)

    challenge_b64 = base64.b64encode(challenge).decode('utf-8')
    post = make_license_request(session, entitlement_body, asset_id, program_id,
                                file_id, challenge_b64)
    log('ccursession/start request: %s' % redact(post))

    headers = api.headers.copy()
    body = session.sign(headers, post)
    data = api.call_api(
        url='https://apigw.cz.vtv.vodafone.com/vtv/ccursession/v1/start',
        data=body, headers=headers)

    if isinstance(data, dict) and 'err' in data:
        # HTTP-level failure. The batch's `session` action may still have taken
        # a session -- store its token out of the error body before raising, or
        # that session leaks with nothing to release it (permanent 1007).
        body_data = parse_error_body(data.get('body'))
        capture_session_token(body_data)
        error = find_api_error(body_data) if body_data else None
        raise WidevineError(
            'license request failed: %s %s' % (data.get('err'),
                                               _one_line(data.get('body'))),
            code=error.get('code') if error else None)

    # 200 response: store the token whether or not the license itself
    # succeeded -- a refused license still leaves a held session behind.
    stored = capture_session_token(data)

    error = find_api_error(data)
    if error:
        raise WidevineError('license request refused: %s' % describe_api_error(error),
                            code=error.get('code'))

    if not stored:
        log('no sessionToken in the start response -- the session cannot be '
            'released later: %s' % redact(data), xbmc.LOGWARNING)

    license_b64 = find_license(data)
    if not license_b64:
        raise WidevineError('no license in the ccursession response: %s'
                            % redact(data))
    log('ccursession/start answered with a %d byte payload' % len(license_b64))
    return base64.b64decode(license_b64)


def _one_line(body):
    if not body:
        return ''
    return ' '.join(str(body).split())


# ---------------------------------------------------------------------------
# challenge variants
# ---------------------------------------------------------------------------
#
# Nagra SSM answers our challenge with `NagraSSMException 1007 Bad request` in
# 0.2 ms -- a local parse/validation refusal, since the very same batch with a
# 4 byte SERVICE_CERTIFICATE_REQUEST challenge is answered normally. The real
# challenges in libs/1.json and libs/2.json carry three things pywidevine never
# sends: protocol_version 2.2, LicenseRequest field 9 (cdm version) and
# SignedMessage field 9 (the OEMCrypto core message). These variants exist to
# find out which of them Nagra insists on -- run "Diagnostika DRM" from the
# addon settings and it stores the winner in `wv_variant`.

VARIANTS = [
    ('pywidevine', dict(protocol_version=21)),
    ('v22', dict(protocol_version=22)),
    ('v22+cdm', dict(protocol_version=22, cdm_version=True)),
    ('v22+cdm+core', dict(protocol_version=22, cdm_version=True,
                          core_message=True, sign_core_message=True)),
    ('v22+cdm+core-unsigned', dict(protocol_version=22, cdm_version=True,
                                   core_message=True, sign_core_message=False)),
    ('v22+cdm+core-plaintext', dict(protocol_version=22, cdm_version=True,
                                    core_message=True, sign_core_message=True,
                                    privacy_mode=False)),
]

# Measured: the plain pywidevine shape is the one this license server accepts.
# Adding the things real CDMs send (2.2, cdm_version, core message) gets the
# challenge refused -- so do not "improve" this without re-running the
# diagnostics.
DEFAULT_VARIANT = 'pywidevine'


def variant_options(name):
    for known, options in VARIANTS:
        if known == name:
            return dict(options)
    return dict(dict(VARIANTS)[DEFAULT_VARIANT])


def build_challenge(cdm, device, init_data, options, privacy_available=True):
    """Turn a variant description into an actual challenge."""
    options = dict(options)
    if options.pop('cdm_version', False):
        options['cdm_version'] = device.cdm_version
    if options.pop('core_message', False):
        options['core_message'] = make_core_message()
    options['privacy_mode'] = options.get('privacy_mode', True) and privacy_available
    return cdm.get_license_challenge(init_data, **options), options


def selected_variant():
    name = (xbmcaddon.Addon().getSetting('wv_variant') or '').strip()
    return name or DEFAULT_VARIANT


# ---------------------------------------------------------------------------
# service certificate
# ---------------------------------------------------------------------------

def cached_certificate_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'service_cert_from_server.bin')


def common_privacy_certificate():
    """The certificate the Chrome CDM uses when no server cert is supplied.

    provider_id `license.widevine.com`, serial 1705b917cc1204868b06333a2f772a8c.
    The working browser challenge in libs/2.json used it -- but that capture is
    from April 2025, so this is a candidate to test, not a known-good default.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'common_privacy_cert.bin')
    with open(path, 'rb') as f:
        return f.read()


def certificate_by_name(name, session, api, entitlement_body, asset_id,
                        program_id, file_id, fallback_cert):
    """One of the certificates we can encrypt the client id to."""
    if name == 'none':
        return None
    if name == 'widevine':
        return common_privacy_certificate()
    if name == 'server':
        return fallback_cert
    return request_service_certificate(session, api, entitlement_body,
                                       asset_id, program_id, file_id)


def request_service_certificate(session, api, entitlement_body, asset_id,
                                program_id, file_id):
    """Ask the license server which certificate it wants (SERVICE_CERTIFICATE_REQUEST).

    Goes through the normal full batch: the gateway rejects a license-only one
    with apigw-11001. The answer is cached, so this costs one extra
    ccursession/start on the first playback only.
    """
    cache = cached_certificate_path()
    if os.path.isfile(cache):
        with open(cache, 'rb') as f:
            cert = f.read()
        if cert:
            log('service certificate loaded from cache')
            return cert

    cert = request_license(session, api, entitlement_body, asset_id,
                           program_id, file_id, SERVICE_CERTIFICATE_CHALLENGE)
    # make sure it really is a certificate before caching it
    parsed = ServiceCertificate.parse(cert)
    log('license server offers %r' % parsed)

    try:
        directory = os.path.dirname(cache)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(cache, 'wb') as f:
            f.write(cert)
    except Exception as e:
        log('could not cache the service certificate: %s' % e, xbmc.LOGWARNING)
    return cert


def get_service_certificate(session, api, entitlement_body, asset_id,
                            program_id, file_id, fallback_cert):
    """The certificate the client id gets encrypted to (privacy mode).

    The captured web player encrypts to `license.widevine.com` while the Nagra
    /certificates endpoint hands out `conax.com`, and a challenge encrypted to
    the conax cert is answered with NagraSSMException 1007 -- so by default ask
    the license server itself, and only fall back to the fetched certificate.

    Returns raw cert bytes (or base64 str), or None to disable privacy mode.
    """
    source = (xbmcaddon.Addon().getSetting('wv_cert_source') or 'auto').strip()

    if source == 'none':
        log('privacy mode disabled by settings')
        return None

    try:
        return certificate_by_name(source, session, api, entitlement_body,
                                   asset_id, program_id, file_id, fallback_cert)
    except Exception as e:
        log('could not get the "%s" service certificate (%s), falling back to '
            'the fetched one' % (source, e), xbmc.LOGWARNING)
        return fallback_cert


# ---------------------------------------------------------------------------
# key cache
# ---------------------------------------------------------------------------
#
# The account allows one playback session at a time and there is no known way
# to release it, so starting a second playback within a few minutes is answered
# `NagraSSMException 1007`. Content keys for a channel stay valid far longer
# than that, so keep the ones we win and reuse them when a license request is
# refused. A successful request always refreshes the cache, so the keys cannot
# quietly go stale while things are working.

KEY_CACHE_TTL = 24 * 60 * 60


def key_cache_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'wv_keys.json')


def load_key_cache():
    try:
        with open(key_cache_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def store_keys(keys):
    cache = load_key_cache()
    now = int(time.time())
    for kid, key in keys.items():
        cache[kid] = {'key': key, 'ts': now}
    try:
        directory = os.path.dirname(key_cache_path())
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(key_cache_path(), 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=1)
    except Exception as e:
        log('could not write the key cache: %s' % e, xbmc.LOGWARNING)


def cached_keys_for(pssh):
    """Fresh cached keys covering every KID this PSSH announces, or None."""
    kids = [_hex(k) for k in pssh.key_ids]
    if not kids:
        return None
    cache = load_key_cache()
    now = int(time.time())
    found = {}
    for kid in kids:
        entry = cache.get(kid)
        if not entry or now - entry.get('ts', 0) > KEY_CACHE_TTL:
            return None
        found[kid] = entry['key']
    return found


def _hex(data):
    return ''.join('%02x' % b for b in bytearray(data))


def rewrite_client_info(client_id, omit, model_name):
    """Rebuild a ClientIdentification, dropping/overriding client_info (field 3).

    Every other field (notably the signed token, field 2) is copied verbatim.
    Returns (new_client_id, dropped_names, overridden_model_or_None).
    """
    from libs.pywidevine import proto
    out = bytearray()
    dropped, overrode = [], None
    for field_no, wire, val in proto.iter_fields(client_id):
        if field_no == 3 and wire == proto.WIRE_BYTES:
            pair = proto.fields_dict(val)
            name = proto.get_one(pair, 1, default=b'').decode('utf-8', 'replace')
            if name in omit:
                dropped.append(name)
                continue
            if name == 'model_name' and model_name:
                overrode = model_name
                val = proto.string_field(1, name) + proto.string_field(2, model_name)
            out += proto.bytes_field(3, val)
        elif wire == proto.WIRE_VARINT:
            out += proto.varint_field(field_no, val)
        elif wire == proto.WIRE_BYTES:
            out += proto.bytes_field(field_no, val)
        else:  # 32/64-bit fields: tag + raw bytes, copied unchanged
            out += proto.encode_varint((field_no << 3) | wire) + val
    return bytes(out), dropped, overrode


def log_device_identity(device):
    """Dump the identity fields the challenge will carry, for debugging.

    Two kinds of field, exactly as they differ on the wire:

    * client_info name/value pairs (device_name, model_name, build_info /
      fingerprint, widevine_cdm_version, ...) -- unsigned and freely editable;
      the license request is re-signed over whatever we send.
    * the device certificate's serial_number / system_id / provider_id -- these
      live in the Google-signed token (ClientIdentification field 2) and cannot
      be changed without invalidating that signature.

    In privacy mode these bytes are still what gets encrypted to the service
    certificate, so this is a faithful picture of what /start receives.
    """
    from libs.pywidevine import proto
    log('device: type=%s security_level=L%s'
        % (device.type_name, device.security_level), xbmc.LOGDEBUG)
    for name, value in sorted(device.client_info.items()):
        log('  client_info %s = %s' % (name, value), xbmc.LOGDEBUG)
    try:
        # ClientIdentification.token (2) -> SignedDrmCertificate.drm_certificate
        # (1) -> DrmCertificate{serial_number=2, system_id=5, provider_id=7}
        token = proto.get_one(proto.fields_dict(device.client_id), 2, default=b'')
        drm_cert = proto.get_one(proto.fields_dict(token), 1, default=b'')
        cert = proto.fields_dict(drm_cert)
        serial = proto.get_one(cert, 2, default=b'')
        system_id = proto.get_one(cert, 5, proto.WIRE_VARINT)
        provider = proto.get_one(cert, 7, default=b'').decode('utf-8', 'replace')
        log('  certificate (signed, fixed): provider_id=%s system_id=%s serial=%s'
            % (provider, system_id, _hex(serial)), xbmc.LOGDEBUG)
    except Exception as e:
        log('  could not parse device certificate: %s' % e, xbmc.LOGWARNING)


# ---------------------------------------------------------------------------
# the whole flow
# ---------------------------------------------------------------------------

def get_content_keys(session, api, entitlement_body, asset_id, program_id,
                     file_id, manifest_url, service_cert, drm_declared = True):
    """Run the license exchange locally and return {kid_hex: key_hex}.

    Returns None when the manifest is not Widevine-protected at all -- the
    stream is in the clear and needs no license, no session and no keys.

    `drm_declared` is what the playback context said about this source. If it
    declared no DRM, a manifest we cannot even fetch is not worth failing on:
    the player is about to fetch it too and can report the real problem.
    """
    # Check the manifest before the device: a clear channel must play even
    # when there is no .wvd at all.
    try:
        pssh_list = fetch_pssh(manifest_url)
    except Exception as e:
        if drm_declared:
            raise
        log('manifest not readable (%s) -- the API declared no DRM on this '
            'source, handing the stream to the player as it is' % e,
            xbmc.LOGWARNING)
        return None
    if not pssh_list:
        return None

    # Load the device -- a local .wvd, or one leased from the wvd-vault when
    # none is present (see load_device). The whole CDM exchange runs locally.
    device = load_device(session)
    log_device_identity(device)

    certificate = get_service_certificate(session, api, entitlement_body,
                                          asset_id, program_id, file_id,
                                          service_cert)
    keys = {}
    fresh = False

    variant = selected_variant()
    for pssh in pssh_list:
        try:
            cdm = Cdm(device)
            cert = cdm.set_service_certificate(certificate)
            log('service certificate: %r' % cert)

            challenge, used = build_challenge(cdm, device, pssh.init_data,
                                              variant_options(variant),
                                              privacy_available=cert is not None)
            privacy_on = used['privacy_mode']
            log('challenge built, %d bytes, variant %s (privacy mode %s)'
                % (len(challenge), variant, 'ON' if privacy_on else 'OFF'))

            license_data = request_license(session, api, entitlement_body,
                                           asset_id, program_id, file_id,
                                           challenge)
            for key in cdm.parse_license(license_data):
                if key.type != 'CONTENT':
                    continue
                keys[key.kid] = key.key
                fresh = True
        except Exception as e:
            # Most likely the one-session-at-a-time limit (1007). Keys outlive
            # a session by a long way, so reuse the ones we already have.
            cached = cached_keys_for(pssh)
            if not cached:
                raise
            log('license request failed (%s) -- reusing %d cached key(s)'
                % (e, len(cached)), xbmc.LOGWARNING)
            keys.update(cached)

    if not keys:
        raise WidevineError('license exchange returned no content keys')
    if fresh:
        store_keys(keys)
    log('got %d content key(s): %s' % (len(keys), ', '.join(keys)))
    return keys

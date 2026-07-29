# -*- coding: utf-8 -*-
"""Signing in by scanning a QR code with the mobile app.

How the official TV app does it, taken from its own code and confirmed
against the service:

  * the TV invents the pin -- there is no endpoint that issues one. Sending
    authentication/v1/pin a pin the service has never seen answers
    `2003 Pin code not exists`, which is simply "not claimed yet".
  * it shows that pin as a deep link into the mobile app, encoded as a QR:
    https://apps.cz.vtv.vodafone.com/settings.RegisterDevice?pin=<pin>
    (the path is a string in the app's dex, the host came off the screen).
  * the phone, already signed in, claims the pin against the account.
  * the TV keeps posting the same pin until the answer carries a session,
    or until the pin expires. The app's own status codes name the cases:
    PinExpired, LoginViaPinNotAllowed, InsideLockTime.

The pin is 20 digits, opening with a unix timestamp, matching what the TV
app puts on screen.
"""
import os
import random
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from libs import qrcode

DEEP_LINK = 'https://apps.cz.vtv.vodafone.com/settings.RegisterDevice?pin=%s'

PIN_TTL = 5 * 60          # the TV app shows a five minute countdown

# Every attempt is PIN_TTL/POLL_SECONDS requests to the same endpoint, and the
# service does keep an InsideLockTime state, so keep this unhurried. Waiting
# five seconds for a code someone is walking over to scan costs nothing.
POLL_SECONDS = 5

# The pending pin exists only as a side effect of this poll, tagged with the
# deviceBrandId we send -- the TV app (das) sends Other_STV(318), the web
# player sends 114. The brand now comes from the auth scheme (scheme_auth), so
# a web pairing registers a web device and a das pairing a das one.

# from AuthParingResultStatusCodeEntity in the TV app, plus 2003 as observed
PIN_NOT_CLAIMED = ('2002', '2003', '2006')
FATAL = {
    '2004': 'Kód vypršel, zkuste to znovu',
    '2009': 'Přihlášení kódem není pro tento účet povoleno',
    '2010': 'Kód má neplatnou délku',
    '2015': 'Příliš mnoho pokusů, zkuste to za chvíli',
}


def log(message, level = xbmc.LOGINFO):
    xbmc.log('Vodafone TV PAIR > ' + message, level)


def new_pin():
    """20 digits: milliseconds since the epoch, then 7 random ones.

    The TV app builds it from System.currentTimeMillis() and a SecureRandom
    (its QRCodeFragment.D() calls exactly those), and the pin it showed --
    1784819271174|5338902 -- splits on that boundary. Seconds plus ten random
    digits also comes to twenty, but puts the timestamp in the wrong place.
    """
    return '%d%07d' % (int(time.time() * 1000), random.SystemRandom().randrange(10 ** 7))


def profile_dir():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    if not os.path.isdir(profile):
        os.makedirs(profile)
    return profile


def qr_path(pin):
    """A fresh filename per pin.

    Kodi keeps textures it has already loaded keyed by path, so reusing one
    name means the second pairing attempt can show the first attempt's code.
    """
    for stale in os.listdir(profile_dir()):
        if stale.startswith('pairing_qr_') and stale.endswith('.png'):
            try:
                os.remove(os.path.join(profile_dir(), stale))
            except Exception:
                pass
    return os.path.join(profile_dir(), 'pairing_qr_%s.png' % pin)


def background_path():
    path = os.path.join(profile_dir(), 'pairing_bg.png')
    if not os.path.isfile(path):
        qrcode.solid_png(path)
    return path


def error_code(data):
    """The API error code out of a /pin answer, as a string."""
    if not isinstance(data, dict):
        return None
    result = data.get('result')
    if isinstance(result, dict) and isinstance(result.get('error'), dict):
        code = result['error'].get('code')
        return str(code) if code is not None else None
    return None


def describe(data):
    """What a /pin answer says, for the log."""
    if not isinstance(data, dict):
        return repr(data)[:200]
    if 'err' in data:
        return 'transport error: %s %s' % (data.get('err'), str(data.get('body'))[:120])
    result = data.get('result')
    if isinstance(result, dict) and isinstance(result.get('error'), dict):
        error = result['error']
        code = str(error.get('code'))
        return '%s %s%s' % (code, error.get('message'),
                            ' [%s]' % NAMES[code] if code in NAMES else '')
    return 'no error, keys: %s' % sorted(data)


# AuthParingResultStatusCodeEntity, from the TV app
NAMES = {'0': 'OK', '1': 'Error', '2': 'InternalError', '1005': 'UserNotInDomain',
         '2002': 'PinNotExists', '2003': 'PinCodeNotExists', '2004': 'PinExpired',
         '2005': 'ValidPin', '2006': 'NoValidPin', '2007': 'SecretIsWrong',
         '2009': 'LoginViaPinNotAllowed', '2010': 'PinNotInTheRightLength',
         '2011': 'PinAlreadyExists', '2015': 'InsideLockTime',
         '2016': 'UserNotActivated', '2017': 'UserAllreadyLoggedIn',
         '2018': 'UserDoubleLogIn', '2019': 'DeviceNotRegistered',
         '2021': 'ErrorOnInitUser', '2023': 'UserNotMasterApproved',
         '2024': 'UserWIthNoDomain', '2025': 'UserDoesNotExist'}


class PairingWindow(xbmcgui.WindowDialog):
    """The QR, the pin under it, and a countdown."""

    MESSAGE = ('Naskenujte QR kód v mobilní aplikaci Vodafone TV,',
               'kde jste přihlášeni: Nastavení / Účet /',
               'Přihlášení k jinému zařízení.')

    def prepare(self, image, pin):
        self.cancelled = False
        width, height = self.getWidth(), self.getHeight()

        qr_size = int(height * 0.62)
        qr_x = int(width * 0.60)
        text_x = int(width * 0.08)
        text_width = qr_x - text_x - 20
        line_height = int(height * 0.05)
        top = int(height * 0.20)

        # A dialog window is transparent, so whatever was on screen shows
        # through the text. Cover it first.
        self.addControl(xbmcgui.ControlImage(0, 0, width, height,
                                             background_path(), aspectRatio = 0))

        # aspectRatio 2 keeps the symbol square whatever the screen is
        self.addControl(xbmcgui.ControlImage(qr_x, (height - qr_size) // 2,
                                             qr_size, qr_size, image,
                                             aspectRatio = 2))

        self.addControl(xbmcgui.ControlLabel(text_x, top, text_width,
                                             line_height, 'Přihlásit se',
                                             font = 'font30'))
        for i, line in enumerate(self.MESSAGE):
            self.addControl(xbmcgui.ControlLabel(
                text_x, top + int(line_height * (1.8 + i)), text_width,
                line_height, line))

        self.pin_label = xbmcgui.ControlLabel(
            text_x, top + int(line_height * 5.4), text_width, line_height,
            ' '.join(pin[i:i + 5] for i in range(0, len(pin), 5)),
            font = 'font30')
        self.addControl(self.pin_label)

        self.countdown = xbmcgui.ControlLabel(
            text_x, top + int(line_height * 7), text_width, line_height, '')
        self.addControl(self.countdown)

    def tick(self, remaining):
        minutes, seconds = divmod(max(0, int(remaining)), 60)
        self.countdown.setLabel('QR kód a PIN vyprší za %d:%02d' % (minutes, seconds))

    def onAction(self, action):
        # 10 = previous menu, 92 = back
        if action.getId() in (9, 10, 92, 216):
            self.cancelled = True
            self.close()


def pair(api, pin = None, scheme = None):
    """Show a code and wait for the phone to claim it.

    Works with either login scheme (verified live in Kodi with both). With no
    scheme given, the configured one is used. With no pin, one is generated, the
    way the TV app does it; a pin can also be given -- one read off another
    device's screen.

    The poll authenticates with that scheme's device header -- the web JWT
    (web) or a Nagra DAS challenge (das, `vtv-authentication: widevine:...`) --
    and whichever it is, that is the identity the claimed pin registers; the
    session right is decoded to match.

    Returns (data, session_key) on success, (None, None) otherwise.
    """
    from libs.session import AUTH_BASE, find_ks, scheme_auth, auth_scheme
    if scheme is None:
        scheme = auth_scheme()

    own = pin is None
    pin = pin or new_pin()
    # inverted: the app's pairing scanner reads only light-on-dark codes
    image = qrcode.png(qrcode.encode(DEEP_LINK % pin), qr_path(pin), invert = True)
    log('waiting for the phone to claim %s pin %s (%s), scheme %s'
        % ('our' if own else 'the given', pin, image, scheme))

    # one device header for the whole attempt; the same material decodes the
    # session right the winning poll returns
    auth_headers, derive, brand = scheme_auth(api, scheme)
    headers = api.headers.copy()
    headers.update(auth_headers)

    window = PairingWindow()
    window.prepare(image, pin)
    window.show()

    monitor = xbmc.Monitor()
    deadline = time.time() + PIN_TTL
    polls = 1
    try:
        while not monitor.abortRequested() and not window.cancelled:
            remaining = deadline - time.time()
            if remaining <= 0:
                log('the pin expired unclaimed')
                xbmcgui.Dialog().notification(
                    'Vodafone TV', 'Kód vypršel, zkuste to znovu',
                    xbmcgui.NOTIFICATION_WARNING, 5000)
                return None, None
            window.tick(remaining)

            data = api.call_api(url = AUTH_BASE + '/pin',
                                data = {'pin': pin, 'deviceBrandId': brand},
                                headers = headers, sensitive = True)
            if find_ks(data):
                log('the pin was claimed, we have a session')
                return data, derive()

            code = error_code(data)
            # every answer, not just the surprising ones: when this does not
            # work the log is the only witness to what the service said
            log('poll %d for pin %s -> %s' % (polls, pin, describe(data)))
            polls += 1

            if code in FATAL:
                # includes InsideLockTime: stop asking rather than deepening
                # whatever lock the service has just applied
                log('pairing refused: %s' % code, xbmc.LOGWARNING)
                xbmcgui.Dialog().notification('Vodafone TV', FATAL[code],
                                              xbmcgui.NOTIFICATION_ERROR, 6000)
                return None, None

            if monitor.waitForAbort(POLL_SECONDS):
                break
        log('pairing cancelled')
        return None, None
    finally:
        window.close()
        # the file stays until the next attempt cleans it up: removing it here
        # can pull the texture out from under the window that is still closing

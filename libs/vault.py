# -*- coding: utf-8 -*-
"""Lease a Widevine device (.wvd) from a wvd-vault server.

When the addon has no local .wvd, it can lease one from a self-hosted wvd-vault
(https://github.com/WallyCZ/wvd-vault). The vault assigns one .wvd exclusively
to this installation's device id and returns the file; we cache it in the
profile directory and from then on use it like any local device -- the whole
CDM exchange still runs locally. If the vault has no free device it answers 409
and we surface that so the user is told to try again later.

Before it hands anything out the vault wants proof that we really are a
Vodafone subscriber, because a .wvd is worth stealing and a shared API key is
not much of a gate. The proof is a challenge-response over the login session
key (see libs/session.py):

  1. we ask the vault for a challenge, telling it our ks;
  2. it answers with the exact request it wants signed -- a household/get body
     carrying our ks and a nonce of its choosing in `clientTag`;
  3. we check that request against a strict whitelist and HMAC-sign it with the
     session key, which never leaves this machine;
  4. the vault replays the signed request to the Vodafone gateway. Only a live
     household comes back if we really hold a valid session, and the household
     id it learns is what the lease is keyed on -- so passing a ks around buys
     nothing, every sharer maps to the same household and the same one device.

The whitelist in _signable() is the load-bearing part: the same session key
signs switchUser, device registration and entitlement calls, so we must never
sign a body the vault picked freely or the vault becomes a signing oracle for
our account.
"""
import os
import ssl
import json
import base64

import xbmc
import xbmcaddon
import xbmcvfs

from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

CACHED_WVD = 'vault_device.wvd'

# The one request we are willing to sign for the vault, and the only fields it
# may put in it. household/get reads the household the ks belongs to and
# changes nothing; clientTag is free-form in the protocol, so it carries the
# vault's nonce.
HOUSEHOLD_GET_URL = ('https://apigw.cz.vtv.vodafone.com/vtv/phoenix/v1/api_v3/'
                     'service/household/action/get')
SIGNABLE_KEYS = frozenset(('apiVersion', 'ks', 'clientTag'))
MAX_SIGNABLE = 4096


def log(message, level=xbmc.LOGINFO):
    xbmc.log('Vodafone TV VAULT > ' + message, level)


class VaultNoDevice(Exception):
    """The vault has no free .wvd right now."""


class VaultHouseholdLimit(Exception):
    """This household already holds as many devices as the vault allows.

    Unlike VaultNoDevice this does not pass with time: either one of the
    household's other devices is freed, or nothing changes.
    """


class VaultRejected(Exception):
    """The vault would not accept our proof of subscription."""


class VaultError(Exception):
    """Transport or protocol failure talking to the vault."""


class _NotSupported(Exception):
    """This vault predates the challenge endpoint (internal)."""


def _addon():
    return xbmcaddon.Addon()


def vault_url():
    return (_addon().getSetting('wv_vault_url') or '').strip()


def is_configured():
    return bool(vault_url())


def _profile_dir():
    profile = xbmcvfs.translatePath(_addon().getAddonInfo('profile'))
    if not os.path.isdir(profile):
        os.makedirs(profile)
    return profile


def cached_path():
    """Where a leased .wvd is stored; found by widevine.get_device_path()."""
    return os.path.join(_profile_dir(), CACHED_WVD)


def _model_name():
    name = (xbmc.getInfoLabel('System.FriendlyName') or '').strip()
    return name or 'Kodi'


def _context():
    """TLS settings for talking to the vault.

    Certificates are verified: the challenge carries our ks, so an intercepted
    exchange would hand an attacker a live session. A LAN deployment with a
    self-signed certificate can opt out with the wv_vault_insecure setting.
    """
    if _addon().getSetting('wv_vault_insecure') == 'true':
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def _post(url, payload):
    """POST json to the vault and return the parsed answer."""
    body = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    key = (_addon().getSetting('wv_vault_key') or '').strip()
    if key:
        headers['X-API-Key'] = key

    request = Request(url, data=body, headers=headers, method='POST')
    try:
        with urlopen(request, timeout=20, context=_context()) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        if e.code == 404:
            raise _NotSupported()
        detail, code = '', ''
        try:
            answer = json.loads(e.read().decode('utf-8'))
            detail = answer.get('error', '')
            code = answer.get('code', '')
        except Exception:
            pass
        if e.code == 409:
            # Two different 409s: the pool is empty (wait), or this household
            # has spent its allowance (waiting will not help).
            if code == 'household_limit':
                raise VaultHouseholdLimit(detail or 'household allowance used up')
            raise VaultNoDevice()
        if e.code == 403:
            raise VaultRejected(detail or 'proof of subscription refused')
        if e.code == 410:
            # Challenge expired or already spent; the caller starts over.
            raise _NotSupported()
        raise VaultError('vault %s: %s' % (e.code, detail or e.reason))
    except URLError as e:
        raise VaultError('vault unreachable (%s): %s' % (vault_url(), e.reason))
    except ValueError as e:
        raise VaultError('vault sent something that is not json: %s' % e)


def _session(session=None):
    """The signed-in session; loaded from disk when the caller has none."""
    if session is not None:
        return session
    from libs.session import Session
    return Session()


def _signable(challenge, ks):
    """The bytes to sign, once the vault's challenge has passed the whitelist.

    Refuses anything that is not the household/get call we expect, so the vault
    can never get an arbitrary request signed with our session key. The body is
    signed exactly as received -- re-serializing it would change the bytes the
    vault is going to send and the signature would not match.
    """
    url = challenge.get('url')
    if url != HOUSEHOLD_GET_URL:
        raise VaultRejected('vault asked us to sign a request to %s' % url)

    body = challenge.get('body')
    if not isinstance(body, str) or not body or len(body) > MAX_SIGNABLE:
        raise VaultRejected('the challenge body is missing or oversized')

    try:
        post = json.loads(body)
    except ValueError:
        raise VaultRejected('the challenge body is not json')
    if not isinstance(post, dict):
        raise VaultRejected('the challenge body is not an object')

    unknown = set(post) - SIGNABLE_KEYS
    if unknown:
        raise VaultRejected('the challenge body carries %s'
                            % ', '.join(sorted(unknown)))
    if post.get('ks') != ks:
        raise VaultRejected('the challenge body carries a foreign ks')
    tag = post.get('clientTag', '')
    if not isinstance(tag, str) or len(tag) > 128:
        raise VaultRejected('the challenge nonce is not a short string')

    return body.encode('utf-8')


def _sign(session_key, payload):
    """HMAC-SHA256 over the exact bytes, the way Session.sign does it."""
    from jose import jwk
    key = jwk.construct({'alg': 'HS256', 'kty': 'oct', 'k': session_key},
                        'HS256')
    return key.sign(payload).hex()


def _prove(session, ident):
    """Run the challenge-response and return the body for the lease request.

    Raises _NotSupported when the vault has no challenge endpoint, so the
    caller can fall back to an unproven request (which an up-to-date vault will
    refuse anyway -- the enforcement lives on the server).
    """
    ks = getattr(session, 'ks', None)
    session_key = getattr(session, 'session_key', None)
    if not ks or not session_key:
        raise VaultRejected('not signed in to Vodafone TV yet')

    challenge = _post(vault_url().rstrip('/') + '/v1/challenge',
                      dict(ident, ks=ks))
    challenge_id = challenge.get('challenge_id')
    if not challenge_id:
        raise VaultError('the vault sent a challenge without an id')

    signature = _sign(session_key, _signable(challenge, ks))
    log('signed the vault challenge %s' % challenge_id)
    return dict(ident, challenge_id=challenge_id, signature=signature)


def fetch(session=None):
    """Lease a .wvd from the vault, cache it and return its path.

    Raises VaultNoDevice when the pool is full, VaultHouseholdLimit when this
    household may not have another device, VaultRejected when the vault would
    not take our proof of subscription, VaultError on other failures.
    """
    from libs.session import device_id
    url = vault_url().rstrip('/') + '/v1/wvd'
    ident = {'device_id': device_id(), 'model_name': _model_name()}

    try:
        post = _prove(_session(session), ident)
    except _NotSupported:
        log('this vault has no /v1/challenge endpoint; asking for a device '
            'without proof of subscription', xbmc.LOGWARNING)
        post = ident

    try:
        data = _post(url, post)
    except _NotSupported:
        # The challenge went stale between the two calls (or the lease endpoint
        # is gone). One fresh attempt, then give up.
        if post is ident:
            raise VaultError('the vault has no /v1/wvd endpoint')
        log('the challenge expired; starting over', xbmc.LOGWARNING)
        data = _post(url, _prove(_session(session), ident))

    wvd_b64 = data.get('wvd')
    if not wvd_b64:
        raise VaultError('vault response carried no .wvd')
    raw = base64.b64decode(wvd_b64)
    path = cached_path()
    with open(path, 'wb') as f:
        f.write(raw)
    log('leased device %s from the vault (%d bytes) -> %s'
        % (data.get('filename'), len(raw), path))
    return path

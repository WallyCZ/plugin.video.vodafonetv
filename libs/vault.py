# -*- coding: utf-8 -*-
"""Lease a Widevine device (.wvd) from a wvd-vault server.

When the addon has no local .wvd, it can lease one from a self-hosted wvd-vault
(https://github.com/WallyCZ/wvd-vault). The vault assigns one .wvd exclusively
to this installation's device id and returns the file; we cache it in the
profile directory and from then on use it like any local device -- the whole
CDM exchange still runs locally. If the vault has no free device it answers 409
and we surface that so the user is told to try again later.
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


def log(message, level=xbmc.LOGINFO):
    xbmc.log('Vodafone TV VAULT > ' + message, level)


class VaultNoDevice(Exception):
    """The vault has no free .wvd right now."""


class VaultError(Exception):
    """Transport or protocol failure talking to the vault."""


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


def fetch():
    """Lease a .wvd from the vault, cache it and return its path.

    Raises VaultNoDevice when the pool is full, VaultError on other failures.
    """
    from libs.session import device_id
    url = vault_url().rstrip('/') + '/v1/wvd'
    body = json.dumps({'device_id': device_id(),
                       'model_name': _model_name()}).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    key = (_addon().getSetting('wv_vault_key') or '').strip()
    if key:
        headers['X-API-Key'] = key

    request = Request(url, data=body, headers=headers, method='POST')
    # The vault is typically plain HTTP on a LAN box; don't let a self-signed
    # cert on an https deployment abort the request.
    context = ssl._create_unverified_context()
    try:
        with urlopen(request, timeout=20, context=context) as response:
            data = json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        if e.code == 409:
            raise VaultNoDevice()
        detail = ''
        try:
            detail = json.loads(e.read().decode('utf-8')).get('error', '')
        except Exception:
            pass
        raise VaultError('vault %s: %s' % (e.code, detail or e.reason))
    except URLError as e:
        raise VaultError('vault unreachable (%s): %s' % (vault_url(), e.reason))

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

# -*- coding: utf-8 -*-
"""The DMS bootstrap config -- where the app gets everything else from.

    GET https://3062.dms-vfp2.ott.kaltura.com/getconfig
        ?username=dms&password=tvinci&appname=<app>&cver=<ver>&udid=<udid>&platform=Other

The answer's `params` carry the gateway URLs, the `InitObj` credentials the TV
API gateway wants, the Nagra license server, the media type ids search filters
on, the nPVR version, and `RIMPublicKey` -- the URL of the RSA key the login
password is encrypted with.

Values are awkwardly shaped: many are lists of single-key dicts, e.g.

    "InitObj": [{"ApiUser": "tvpapi_3062"}, {"ApiPass": "..."}, ...]

so `flatten()` turns those into a plain dict.

Nothing here is required -- every caller keeps its previous hardcoded value as
a fallback, so a failed fetch or a changed shape cannot break the addon.
"""
import json
import os
import ssl
import time

from urllib.parse import urlencode
from urllib.request import urlopen, Request

import xbmc
import xbmcaddon
import xbmcvfs

GETCONFIG_URL = 'https://3062.dms-vfp2.ott.kaltura.com/getconfig'
APP_NAME = 'com.kaltura.vodafone.group.cz.web.pc.edge'
CLIENT_VERSION = '0.86.0'
TTL = 24 * 60 * 60

_cache = None


def log(message, level = xbmc.LOGINFO):
    xbmc.log('Vodafone TV DMS > ' + message, level)


def cache_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'dms_config.json')


def fetch():
    from libs.session import device_id

    query = urlencode({'username': 'dms', 'password': 'tvinci',
                       'appname': APP_NAME, 'cver': CLIENT_VERSION,
                       'udid': device_id(), 'platform': 'Other'})
    context = ssl._create_unverified_context()
    request = Request('%s?%s' % (GETCONFIG_URL, query),
                      headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=15, context=context) as response:
        data = json.loads(response.read().decode('utf-8'))
    if not isinstance(data, dict) or 'params' not in data:
        raise ValueError('unexpected getconfig response')
    return data


def config(force = False):
    """The cached config dict, refetched once a day. {} when unavailable."""
    global _cache
    if _cache is not None and not force:
        return _cache

    path = cache_path()
    if not force:
        try:
            if os.path.isfile(path) and time.time() - os.path.getmtime(path) < TTL:
                with open(path, 'r', encoding='utf-8') as f:
                    _cache = json.load(f)
                return _cache
        except Exception:
            pass

    try:
        _cache = fetch()
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_cache, f)
        log('config fetched (%d params)' % len(_cache.get('params') or {}))
    except Exception as e:
        log('could not fetch the config (%s) -- using the built-in defaults' % e,
            xbmc.LOGWARNING)
        # a stale cache still beats nothing
        try:
            with open(path, 'r', encoding='utf-8') as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def flatten(value):
    """[{"a": 1}, {"b": 2}] -> {"a": 1, "b": 2}; dicts pass through."""
    if isinstance(value, dict):
        return value
    out = {}
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                out.update(entry)
    return out


def param(name, default = None):
    return (config().get('params') or {}).get(name, default)


def sub(name, key, default = None):
    """One key out of a list-of-single-key-dicts parameter."""
    return flatten(param(name) or []).get(key, default)


# ---------------------------------------------------------------------------
# the handful of values the addon actually uses
# ---------------------------------------------------------------------------

def password_key_url(default):
    return param('RIMPublicKey') or default


def tvapi_gateway(default):
    return sub('Gateways', 'JsonGW', default)


def init_obj_credentials(default_user, default_pass, default_platform):
    init = flatten(param('InitObj') or [])
    return (init.get('ApiUser', default_user),
            init.get('ApiPass', default_pass),
            init.get('Platform', default_platform))


def init_obj_locale(default):
    locale = flatten((flatten(param('InitObj') or []) or {}).get('Locale') or [])
    return locale or default


def nagra_certificate_url(default):
    """`SSPLicenseServerUrl` + tenant -> the Widevine service certificate."""
    base = sub('NagraSettings', 'SSPLicenseServerUrl')
    tenant = sub('NagraSettings', 'TenantID')
    if base and tenant:
        return '%s%s/wvls/contentlicenseservice/v1/certificates' % (
            base if base.endswith('/') else base + '/', tenant)
    return default


def npvr_version(default = 2):
    try:
        return int(param('npvrVersion', default))
    except (TypeError, ValueError):
        return default


def search_filter_types(default):
    """Search's filter_types out of MediaTypes.

    The captured request sends [736, 737, 0, 740] -- Movie, Episode, 0, Folder
    -- so the ids are looked up by name and emitted in that same order rather
    than sorted.
    """
    types = flatten(param('MediaTypes') or [])
    by_name = {}
    for type_id, name in types.items():
        try:
            by_name[name] = int(type_id)
        except (TypeError, ValueError):
            pass

    order = ('Movie', 'Episode', None, 'Folder')
    ids = []
    for name in order:
        if name is None:
            ids.append(0)
        elif name in by_name:
            ids.append(by_name[name])
    return ids if len(ids) == len(order) else default


def epg_days(default_back = 7, default_forward = 7):
    def as_int(value, fallback):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback
    return (as_int(param('epgBckwdDays'), default_back),
            as_int(param('epgFwdDays'), default_forward))

# -*- coding: utf-8 -*-
"""Household profiles.

The web player asks which profile to use right after logging in
(login.har):

    household/action/get   -> the household's users and its masterUsers
    GetUsersData           {"sSiteGuid": "<id>;<id>;<id>"}
    switchUser             {"userIdToSwitch": "<id>"}

`GetUsersData` takes every user id in the household, semicolon separated, and
returns the details. Its response shape is not captured anywhere here, so the
names are read tolerantly and the raw answer is logged -- if a profile shows
up as a bare number, that log line says which field holds the name.

Picking a profile stores the id and drops the session, so the next login
switches to it.
"""
import sys

import xbmc
import xbmcgui
import xbmcplugin

from libs import tvapi
from libs.api import API
from libs.session import Session, selected_profile, store_profile
from libs.utils import get_url, apiVersion

if len(sys.argv) > 1:
    _handle = int(sys.argv[1])

HOUSEHOLD_URL = 'https://apigw.cz.vtv.vodafone.com/vtv/phoenix/v1/api_v3/service/household/action/get'

# GetUsersData answers in the old Tvinci shape -- [{"m_RespStatus": 0,
# "m_user": {"m_oBasicData": {"m_sFirstName": ..., "m_sSiteGuid": ...}, ...}}]
# -- so both the name and the id sit several levels down, under hungarian
# names. Rather than hardcode a path that may differ per deployment, look for
# the first non-empty value whose key *contains* one of these, most specific
# first.
NAME_FIELDS = ('nickname', 'firstname', 'displayname', 'username', 'lastname', 'name')
ID_FIELDS = ('siteguid', 'userguid', 'userid', 'guid', 'id')


def log(message, level = xbmc.LOGINFO):
    xbmc.log('Vodafone TV PROF > ' + message, level)


def household_users(session):
    """(user ids, master id) for the household."""
    api = API()
    headers = api.headers.copy()
    body = session.sign(headers, {'apiVersion': apiVersion, 'ks': session.ks})
    data = api.call_api(url = HOUSEHOLD_URL, data = body, headers = headers)
    result = (data or {}).get('result') if isinstance(data, dict) else None
    if not result or 'users' not in result:
        raise RuntimeError('household/get failed: %s' % data)
    users = [u['id'] for u in result.get('users', [])]
    masters = [u['id'] for u in result.get('masterUsers', [])]
    return users, (masters[0] if masters else None)


def users_data(session, user_ids):
    """Details for the given users -- names, mostly."""
    data = tvapi.call(session, 'GetUsersData',
                      {'sSiteGuid': ';'.join(str(u) for u in user_ids)})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('users', 'Users', 'usersData', 'UsersData'):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def normalise(key):
    """m_sFirstName -> msfirstname, so `firstname` can be found inside it."""
    return ''.join(c for c in key.lower() if c.isalnum())


def walk(value, depth = 0):
    """Every (key, scalar) pair in a nested structure."""
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                for pair in walk(item, depth + 1):
                    yield pair
            elif isinstance(key, str):
                yield key, item
    elif isinstance(value, list):
        for item in value:
            for pair in walk(item, depth + 1):
                yield pair


def field(entry, names):
    """The first non-empty value whose key contains one of `names`.

    `names` is in priority order, so a nickname beats a first name beats a
    bare "name", wherever in the tree they happen to live.
    """
    if not isinstance(entry, (dict, list)):
        return None
    pairs = [(normalise(k), v) for k, v in walk(entry)
             if isinstance(v, (str, int)) and str(v).strip() not in ('', '0')]
    for wanted in names:
        for key, value in pairs:
            if wanted in key:
                return value
    return None


def dump_users_data(entries):
    """Keep the raw answer -- it is the only way to see where the names hide."""
    try:
        import json
        import os
        import xbmcaddon
        import xbmcvfs
        profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
        if not os.path.isdir(profile):
            os.makedirs(profile)
        with open(os.path.join(profile, 'users_data.json'), 'w', encoding='utf-8') as f:
            json.dump(entries, f, indent=1, ensure_ascii=False)
    except Exception as e:
        log('could not dump GetUsersData: %s' % e, xbmc.LOGWARNING)


def profile_label(entry, user_id, master_id, current_id):
    is_master = master_id is not None and str(user_id) == str(master_id)
    name = field(entry or {}, NAME_FIELDS)
    marks = []

    if not name:
        # The master profile usually carries no name at all -- its entry holds
        # nothing but dates and the site guid -- so say what it is rather than
        # showing a bare number.
        name = 'Hlavní profil' if is_master else 'Profil %s' % user_id
    elif is_master:
        marks.append('hlavní')

    if current_id is not None and str(user_id) == str(current_id):
        marks.append('aktivní')
    return '%s%s' % (name, ' (%s)' % ', '.join(marks) if marks else '')


def collect(session):
    """[(user_id, label)] for every profile in the household."""
    users, master = household_users(session)
    details = {}
    if users:
        try:
            entries = users_data(session, users)
            dump_users_data(entries)
            for entry in entries:
                key = field(entry, ID_FIELDS)
                if key is not None:
                    details[str(key)] = entry
            missing = [u for u in users if str(u) not in details]
            if missing and len(entries) == len(users):
                # ids not found in the payload, but one entry per user came
                # back -- assume they are in the order we asked for
                log('matching GetUsersData entries positionally', xbmc.LOGWARNING)
                details = dict((str(u), e) for u, e in zip(users, entries))
        except Exception as e:
            log('GetUsersData failed (%s) -- falling back to ids' % e, xbmc.LOGWARNING)

    current = selected_profile() or getattr(session, 'user_id', None)
    return [(u, profile_label(details.get(str(u)), u, master, current)) for u in users]


def list_profiles(label):
    """Show the household's profiles and switch to the chosen one."""
    xbmcplugin.setPluginCategory(_handle, label)
    try:
        session = Session()
        profiles = collect(session)
    except Exception as e:
        log('could not read the profiles: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Profily se nepodařilo načíst',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.endOfDirectory(_handle)
        return

    log('%d profile(s): %s' % (len(profiles), ', '.join(l for _, l in profiles)))
    for user_id, text in profiles:
        list_item = xbmcgui.ListItem(label = text)
        xbmcplugin.addDirectoryItem(_handle,
            get_url(action = 'switch_profile', id = user_id), list_item, False)
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


def switch_profile(user_id):
    """Remember the profile and start a session as that user."""
    from libs.settings import Settings

    store_profile(user_id)
    # the cached ks belongs to the old profile
    Settings().reset_json_data({'filename': 'session.txt', 'description': 'session'})
    try:
        session = Session()
    except SystemExit:
        raise
    except Exception as e:
        log('could not switch the profile: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Přepnutí profilu selhalo',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    xbmcgui.Dialog().notification('Vodafone TV', 'Profil přepnut',
                                  xbmcgui.NOTIFICATION_INFO, 4000)
    xbmc.executebuiltin('Container.Refresh')

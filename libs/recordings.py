# -*- coding: utf-8 -*-
"""Recordings (nPVR), through the TV API gateway.

Captured from the web player (recording_req.har):

    RecordAsset             {"epgId": "574239415"}
    CancelAssetRecording    {"recordingId": "1874425827"}
    RecordSeriesBySeriesId  {"seriesId": "IPS346228S", "channelId": 5095,
                             "lookupCriteria": ["AnyDayAnyTime"]}
    CancelSeriesRecording   {"seriesRecordingId": "12659886", "version": 2}
    GetRecordings           {"searchBy": "ByRecordingStatus",
                             "recordingStatus": "Scheduled"|"Ongoing"|"Completed",
                             "recordedEPGOrderObj": {...}, "pageSize": .., "version": 2}
    GetSeriesRecordings     {"recordedEPGOrderObj": {...}, "pageSize": 50, "version": 2}

`GetRecordings` answers with a bare JSON list. Each entry carries
`RecordingID`, `STATUS`, `EPG_CHANNEL_ID` (matching a channel's externalIds)
and an `EPG_TAGS` list of Key/Value pairs holding millisecond timestamps --
`START_DATE`/`END_DATE` are local strings with no zone, so the tags are
preferred.

The planning screens below (channel -> day -> upcoming programme) are the
original ones; only the API calls behind them changed.
"""
import os
import sys
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

from datetime import date, datetime, timedelta
import time

from libs import tvapi
from libs import dms
from libs.session import Session
from libs.channels import Channels
from libs.epg import epg_listitem, get_channel_epg
from libs.utils import get_url, plugin_id, day_translation, day_translation_short
from libs import logos

if len(sys.argv) > 1:
    _handle = int(sys.argv[1])

ORDER = {'m_eOrderBy': 'StartTime', 'm_eOrderDir': 'ASC'}


def log(message, level = xbmc.LOGINFO):
    xbmc.log('Vodafone TV REC > ' + message, level)


# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------

def get_recordings(session, status = None, page_size = 100):
    body = {'recordedEPGOrderObj': ORDER, 'pageSize': page_size, 'pageIndex': 0,
            'version': dms.npvr_version()}
    if status:
        body.update({'searchBy': 'ByRecordingStatus', 'recordingStatus': status})
    data = tvapi.call(session, 'GetRecordings', body)
    return data if isinstance(data, list) else []


def get_series_recordings(session, page_size = 50):
    data = tvapi.call(session, 'GetSeriesRecordings',
                      {'recordedEPGOrderObj': ORDER, 'pageSize': page_size,
                       'pageIndex': 0, 'version': dms.npvr_version()})
    return data if isinstance(data, list) else []


def record_asset(session, epg_id):
    return tvapi.call(session, 'RecordAsset', {'epgId': str(epg_id)})


def cancel_asset_recording(session, recording_id):
    return tvapi.call(session, 'CancelAssetRecording', {'recordingId': str(recording_id)})


def delete_asset_recording(session, recording_id):
    """Remove a recording that has already been made.

    `CancelAssetRecording` only cancels a *scheduled* one -- on a finished
    recording it answers `AssetAlreadyRecorded`. This method name is a guess:
    no capture of deleting a completed recording exists, so if it turns out to
    be wrong the gateway says so and the error is reported as-is.
    """
    return tvapi.call(session, 'DeleteAssetRecording', {'recordingId': str(recording_id)})


def record_series(session, series_id, channel_external_id):
    return tvapi.call(session, 'RecordSeriesBySeriesId',
                      {'seriesId': str(series_id),
                       'channelId': int(channel_external_id),
                       'lookupCriteria': ['AnyDayAnyTime']})


def cancel_series_recording(session, series_recording_id):
    return tvapi.call(session, 'CancelSeriesRecording',
                      {'seriesRecordingId': str(series_recording_id), 'version': dms.npvr_version()})


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def tag(recording, key, default = None):
    for entry in recording.get('EPG_TAGS') or []:
        if entry.get('Key') == key:
            return entry.get('Value')
    return default


def recording_times(recording):
    """(start, end) epoch seconds, from the millisecond EPG_TAGS when present."""
    start, end = tag(recording, 'startTime'), tag(recording, 'endTime')
    try:
        if start and end:
            return int(start) // 1000, int(end) // 1000
    except (TypeError, ValueError):
        pass
    try:
        fmt = '%Y%m%d%H%M%S'
        return (int(time.mktime(datetime.strptime(recording['START_DATE'], fmt).timetuple())),
                int(time.mktime(datetime.strptime(recording['END_DATE'], fmt).timetuple())))
    except Exception:
        return None, None


def channel_for(recording, channels_list):
    external = str(recording.get('EPG_CHANNEL_ID') or '')
    for channel_id, channel in channels_list.items():
        if str(channel.get('externalIds') or '') == external:
            return channel_id, channel
    return None, None


def best_image(recording):
    pictures = recording.get('EPG_PICTURES') or []
    for ratio in ('bg', 'cc'):
        candidates = [p for p in pictures if p.get('Ratio') == ratio]
        if candidates:
            return max(candidates, key = lambda p: p.get('PicWidth') or 0).get('Url')
    return recording.get('PIC_URL')


def recording_title(recording):
    return (recording.get('ProgrammeName') or tag(recording, 'seriesName')
            or recording.get('NAME') or '')


def recording_listitem(recording, channels_list):
    """(ListItem, url) for one recording; url is None when it cannot be played."""
    start, end = recording_times(recording)
    channel_id, channel = channel_for(recording, channels_list)
    title = recording_title(recording)
    status = (recording.get('STATUS') or '').lower()

    parts = [title]
    parts.append(channel['name'] if channel else recording.get('ChannelName'))
    if start:
        parts.append(day_translation_short[datetime.fromtimestamp(start).strftime('%w')]
                     + ' ' + datetime.fromtimestamp(start).strftime('%d.%m. %H:%M'))
    if status and status != 'completed':
        parts.append(status)

    list_item = xbmcgui.ListItem(label = ' | '.join(p for p in parts if p))
    image = best_image(recording)
    list_item.setArt({'thumb': image, 'icon': image})
    list_item.setInfo('video', {'mediatype': 'movie', 'title': title,
                                'plot': recording.get('DESCRIPTION') or ''})
    list_item.setContentLookup(False)
    list_item.addContextMenuItems([('Smazat nahrávku',
        'RunPlugin(plugin://' + plugin_id + '?action=delete_recording&id='
        + str(recording.get('RecordingID')) + ')')])

    # Only a programme that has already aired can be played, and it goes
    # through the ordinary CATCHUP path: the recording carries no Kaltura asset
    # id, so play_recording looks the programme up by channel and window.
    url = None
    if channel_id is not None and start and end and end < int(time.time()):
        list_item.setProperty('IsPlayable', 'true')
        url = get_url(action = 'play_recording', channel_id = channel_id,
                      startts = start, endts = end)
    return list_item, url


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------

def list_recordings(label):
    xbmcplugin.setPluginCategory(_handle, label)

    list_item = xbmcgui.ListItem(label='Plánování nahrávek')
    xbmcplugin.addDirectoryItem(_handle,
        get_url(action='list_planning_recordings', label = label + ' / ' + 'Plánování'),
        list_item, True)
    list_item = xbmcgui.ListItem(label='Naplánované nahrávky')
    xbmcplugin.addDirectoryItem(_handle,
        get_url(action='list_future_recordings', label = label + ' / ' + 'Naplánované nahrávky'),
        list_item, True)
    list_item = xbmcgui.ListItem(label='Seriálové nahrávky')
    xbmcplugin.addDirectoryItem(_handle,
        get_url(action='list_series_recordings', label = label + ' / ' + 'Seriálové nahrávky'),
        list_item, True)

    session = Session()
    channels_list = Channels().get_channels_list('id', visible_filter = False)
    try:
        recordings = get_recordings(session, 'Completed')
    except Exception as e:
        log('could not list recordings: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Nahrávky se nepodařilo načíst',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)
        return

    recordings.sort(key = lambda r: recording_times(r)[0] or 0, reverse = True)
    log('%d completed recording(s)' % len(recordings))
    for recording in recordings:
        item, url = recording_listitem(recording, channels_list)
        if url:
            xbmcplugin.addDirectoryItem(_handle, url, item, False)
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


def list_future_recordings(label):
    xbmcplugin.setPluginCategory(_handle, label)
    session = Session()
    channels_list = Channels().get_channels_list('id', visible_filter = False)
    try:
        recordings = get_recordings(session, 'Scheduled') + get_recordings(session, 'Ongoing')
    except Exception as e:
        log('could not list scheduled recordings: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Nahrávky se nepodařilo načíst',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)
        return

    recordings.sort(key = lambda r: recording_times(r)[0] or 0)
    log('%d scheduled recording(s)' % len(recordings))
    for recording in recordings:
        item, url = recording_listitem(recording, channels_list)
        item.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(_handle, get_url(action='list_recordings',
                                                     label='Nahrávky'), item, False)
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


def first_of(item, *keys):
    """Field names differ in case between the gateway's responses."""
    lowered = dict((k.lower(), v) for k, v in item.items())
    for key in keys:
        value = lowered.get(key.lower())
        if value:
            return value
    return None


def series_names(session):
    """seriesId -> name.

    `GetSeriesRecordings` returns `seriesName: null`, so the names have to come
    from the episode entries, whose EPG_TAGS carry both seriesId and seriesName.
    """
    names = {}
    for status in ('Scheduled', 'Completed'):
        try:
            for recording in get_recordings(session, status):
                series_id, name = tag(recording, 'seriesId'), tag(recording, 'seriesName')
                if series_id and name:
                    names.setdefault(series_id, name)
        except Exception as e:
            log('could not read %s recordings for series names: %s' % (status, e),
                xbmc.LOGWARNING)
    return names


def list_series_recordings(label):
    xbmcplugin.setPluginCategory(_handle, label)
    session = Session()
    try:
        series = get_series_recordings(session)
    except Exception as e:
        log('could not list series recordings: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Seriálové nahrávky se nepodařilo načíst',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)
        return

    log('%d series recording(s)' % len(series))
    names = series_names(session) if series else {}
    channels_list = Channels().get_channels_list('id', visible_filter = False)

    for item in series:
        series_id = first_of(item, 'seriesID', 'seriesId', 'SeriesId')
        recording_id = first_of(item, 'recordingID', 'RecordingID',
                                'seriesRecordingId', 'SeriesRecordingId', 'id')
        title = (first_of(item, 'seriesName', 'SeriesName', 'NAME')
                 or names.get(series_id) or str(series_id or 'Seriál'))

        channel = None
        external = str(first_of(item, 'epgChannelID', 'EPG_CHANNEL_ID') or '')
        for candidate in channels_list.values():
            if str(candidate.get('externalIds') or '') == external:
                channel = candidate
                break
        if channel:
            title = '%s | %s' % (title, channel['name'])

        list_item = xbmcgui.ListItem(label = title)
        list_item.setInfo('video', {'mediatype': 'tvshow', 'title': title})
        if channel:
            list_item.setArt({'thumb': channel['logo'], 'icon': channel['logo']})
        if recording_id:
            list_item.addContextMenuItems([('Zrušit nahrávání seriálu',
                'RunPlugin(plugin://' + plugin_id + '?action=delete_series_recording&id='
                + str(recording_id) + ')')])
        else:
            log('series entry without an id: %s' % item, xbmc.LOGWARNING)
        xbmcplugin.addDirectoryItem(_handle,
            get_url(action='list_recordings', label='Nahrávky'), list_item, False)
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

def add_recording(id):
    try:
        record_asset(Session(), id)
    except Exception as e:
        log('could not add the recording: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Problém s přidáním nahrávky',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return
    xbmcgui.Dialog().notification('Vodafone TV', 'Nahrávka přidána',
                                  xbmcgui.NOTIFICATION_INFO, 5000)


def delete_recording(id):
    session = Session()
    try:
        try:
            cancel_asset_recording(session, id)
        except Exception as e:
            # A finished recording cannot be cancelled, only removed.
            if 'AssetAlreadyRecorded' not in str(e):
                raise
            log('already recorded, removing instead: %s' % e)
            delete_asset_recording(session, id)
    except Exception as e:
        log('could not delete the recording: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Problém se smazáním nahrávky',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return
    xbmcgui.Dialog().notification('Vodafone TV', 'Nahrávka smazána',
                                  xbmcgui.NOTIFICATION_INFO, 5000)
    xbmc.executebuiltin('Container.Refresh')


# scheduled recordings are cancelled with the same call
delete_future_recording = delete_recording


def delete_series_recording(id):
    try:
        cancel_series_recording(Session(), id)
    except Exception as e:
        log('could not cancel the series recording: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Problém se zrušením nahrávání seriálu',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return
    xbmcgui.Dialog().notification('Vodafone TV', 'Nahrávání seriálu zrušeno',
                                  xbmcgui.NOTIFICATION_INFO, 5000)
    xbmc.executebuiltin('Container.Refresh')


# ---------------------------------------------------------------------------
# planning (unchanged: channel -> day -> upcoming programme)
# ---------------------------------------------------------------------------

def list_planning_recordings(label):
    addon = xbmcaddon.Addon()
    xbmcplugin.setPluginCategory(_handle, label)
    channels = Channels()
    channels_list = channels.get_channels_list('channel_number')
    cnt = 0
    for number in sorted(channels_list.keys()):
        cnt += 1
        if addon.getSetting('channel_numbers') == 'číslo kanálu':
            channel_number = str(number) + '. '
        elif addon.getSetting('channel_numbers') == 'pořadové číslo':
            channel_number = str(cnt) + '. '
        else:
            channel_number = ''
        list_item = xbmcgui.ListItem(label = channel_number + channels_list[number]['name'])
        logo = logos.logo_for(channels_list[number])
        list_item.setArt({'thumb': logo, 'icon': logo})
        url = get_url(action='list_rec_days', id = channels_list[number]['id'], label = label + ' / ' + channels_list[number]['name'])
        xbmcplugin.addDirectoryItem(_handle, url, list_item, True)
    xbmcplugin.endOfDirectory(_handle)


def list_rec_days(id, label):
    xbmcplugin.setPluginCategory(_handle, label)
    for i in range (8):
        day = date.today() + timedelta(days = i)
        if i == 0:
            den_label = 'Dnes'
            den = 'Dnes'
        elif i == 1:
            den_label = 'Zítra'
            den = 'Zítra'
        else:
            den_label = day_translation_short[day.strftime('%w')] + ' ' + day.strftime('%d.%m.')
            den = day_translation[day.strftime('%w')] + ' ' + day.strftime('%d.%m.%Y')
        list_item = xbmcgui.ListItem(label=den)
        url = get_url(action='future_program', id = id, day = i, label = label + ' / ' + den_label)
        xbmcplugin.addDirectoryItem(_handle, url, list_item, True)
    xbmcplugin.endOfDirectory(_handle)


def future_program(id, day, label):
    addon = xbmcaddon.Addon()
    icons_dir = os.path.join(addon.getAddonInfo('path'), 'resources','images')

    label = label.replace('Nahrávky / Plánování /', '')
    xbmcplugin.setPluginCategory(_handle, label)
    id = int(id)
    today_date = datetime.today()
    today_start_ts = int(time.mktime(datetime(today_date.year, today_date.month, today_date.day).timetuple()))
    today_end_ts = today_start_ts + 60*60*24 -1
    if int(day) == 0:
        from_ts = int(time.mktime(datetime.now().timetuple()))
        to_ts = today_end_ts
    else:
        from_ts = today_start_ts + int(day)*60*60*24
        to_ts = today_end_ts + int(day)*60*60*24
    epg = get_channel_epg(id, from_ts, to_ts)

    if int(day) >  0:
        list_item = xbmcgui.ListItem(label='Předchozí den')
        day_dt = date.today() + timedelta(days = int(day) - 1)
        den_label = day_translation_short[day_dt.strftime('%w')] + ' ' + day_dt.strftime('%d.%m.')
        url = get_url(action='future_program', id = id, day = int(day) - 1, label = label.rsplit(' / ')[0] + ' / ' + den_label)
        list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'previous_arrow.png'), 'icon' : os.path.join(icons_dir , 'previous_arrow.png') })
        xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    for key in sorted(epg.keys()):
        start = epg[key]['startts']
        end = epg[key]['endts']
        list_item = xbmcgui.ListItem(label = day_translation_short[datetime.fromtimestamp(start).strftime('%w')] + ' ' + datetime.fromtimestamp(start).strftime('%d.%m. %H:%M') + ' - ' + datetime.fromtimestamp(end).strftime('%H:%M') + ' | ' + epg[key]['title'])
        list_item = epg_listitem(list_item, epg[key], '')
        list_item.setProperty('IsPlayable', 'false')
        list_item.addContextMenuItems([('Přidat nahrávku', 'RunPlugin(plugin://' + plugin_id + '?action=add_recording&id=' + str(epg[key]['id']) + ')',)])
        url = get_url(action='add_recording', id = epg[key]['id'])
        xbmcplugin.addDirectoryItem(_handle, url, list_item, False)

    if int(day) <  7:
        list_item = xbmcgui.ListItem(label='Následující den')
        day_dt = date.today() + timedelta(days = int(day) + 1)
        den_label = day_translation_short[day_dt.strftime('%w')] + ' ' + day_dt.strftime('%d.%m.')
        url = get_url(action='future_program', id = id, day = int(day) + 1, label = label.rsplit(' / ')[0] + ' / ' + den_label)
        list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'next_arrow.png'), 'icon' : os.path.join(icons_dir , 'next_arrow.png') })
        xbmcplugin.addDirectoryItem(_handle, url, list_item, True)
    xbmcplugin.endOfDirectory(_handle, updateListing = True)

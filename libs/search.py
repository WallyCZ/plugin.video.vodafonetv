# -*- coding: utf-8 -*-
import sys
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
from xbmcvfs import translatePath

from urllib.parse import quote, quote_plus

from datetime import datetime
import json
import time

from libs.utils import get_url, plugin_id, day_translation_short
from libs.session import Session
from libs.channels import Channels
from libs.epg import epg_listitem
from libs import tvapi
from libs import dms
from libs import entitlement

_handle = int(sys.argv[1])

def list_search(label):
    xbmcplugin.setPluginCategory(_handle, label)
    list_item = xbmcgui.ListItem(label='Nové hledání')
    url = get_url(action='program_search', query = '-----', label = label + ' / ' + 'Nové hledání')  
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)
    history = load_search_history()
    for item in history:
        list_item = xbmcgui.ListItem(label=item)
        url = get_url(action='program_search', query = item, label = label + ' / ' + item)  
        list_item.addContextMenuItems([('Smazat', 'RunPlugin(plugin://' + plugin_id + '?action=delete_search&query=' + quote(item) + ')')])
        xbmcplugin.addDirectoryItem(_handle, url, list_item, True)
    xbmcplugin.endOfDirectory(_handle,cacheToDisc = False)

SEARCH_METHOD = 'GetExternalSearchMedias'

# The fields the web player asks for; the interesting ones come back anyway,
# but the gateway wants an `fl` list.
SEARCH_FIELDS = ('id,availVodOffers.altIds,availLinearOffers.altIds,vodOffers.altIds,'
                 'linearOffers.altIds,availPurchases.itemId,availRecordings')


def search_assets(session, query, page_size = 50, page_index = 0):
    """Full-text search through the TV API gateway.

    The Kaltura asset/list the addon used before does not do free text at all
    -- the web player sends the query to jsonpostgw.aspx, which answers with
    an `assets` list. Same HMAC headers as everything else.
    """
    data = tvapi.call(session, SEARCH_METHOD, {
        'query': 'q=%s&fl=%s&useCase=3&brandId=114' % (quote_plus(query), quote_plus(SEARCH_FIELDS)),
        'pageIndex': page_index,
        'pageSize': page_size,
        'filter_types': dms.search_filter_types([736, 737, 0, 740]),
        'UTC-offset': utc_offset_hours(),
        'With': ['files'],
    })
    return (data or {}).get('assets') or []


def utc_offset_hours():
    """The gateway takes the client's UTC offset in whole hours, as a string."""
    offset = -(time.altzone if time.daylight and time.localtime().tm_isdst else time.timezone)
    return str(int(offset / 3600))


def programme_from_asset(asset, channels_list):
    """Turn a search hit into the epg dict the rest of the addon passes around.

    Only linear programmes are playable here: they carry start/end dates and an
    `epg_channel_id` that matches a channel's externalIds. VOD hits and
    programmes on channels we do not have are dropped.
    """
    try:
        start = int(asset.get('start_date') or 0)
        end = int(asset.get('end_date') or 0)
    except (TypeError, ValueError):
        return None
    if not start or not end:
        return None

    external = str((asset.get('extra_params') or {}).get('epg_channel_id') or '')
    channel_id = None
    for cid, channel in channels_list.items():
        if str(channel.get('externalIds') or '') == external:
            channel_id = cid
            break
    if channel_id is None:
        return None

    metas = asset.get('metas') or {}
    image = None
    for wanted in ('bg', 'cc'):
        candidates = [i for i in (asset.get('images') or []) if i.get('ratio') == wanted]
        if candidates:
            image = max(candidates, key = lambda i: i.get('width') or 0).get('url')
            break

    return {
        'id': asset.get('id'),
        'channel_id': channel_id,
        'title': asset.get('name') or '',
        'plot': asset.get('description') or '',
        'startts': start,
        'endts': end,
        'image': image,
        'year': metas.get('year'),
        'episode': metas.get('episode name'),
    }


def program_search(query, label):
    xbmcplugin.setPluginCategory(_handle, label)
    if query == '-----':
        input = xbmc.Keyboard('', 'Hledat')
        input.doModal()
        if not input.isConfirmed():
            return
        query = input.getText()
        if len(query) == 0:
            xbmcgui.Dialog().notification('Vodafone TV', 'Je potřeba zadat vyhledávaný řetězec', xbmcgui.NOTIFICATION_ERROR, 5000)
            return
        else:
            save_search_history(query)
    session = Session()
    channels_list = Channels().get_channels_list('id', visible_filter = True)
    try:
        assets = search_assets(session, query)
    except Exception as e:
        xbmc.log('Vodafone TV > search failed: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Vyhledávání selhalo', xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    # The search gateway (useCase=3) already collapses a series into a single
    # hit that carries its series id. Such a hit becomes a folder that expands,
    # via list_series_episodes, to every broadcast of the series; standalone
    # titles (films, one-off programmes, VOD) render inline as playable items.
    hide_unsubscribed = entitlement.hide_enabled()
    statuses = {}
    if hide_unsubscribed:
        statuses = entitlement.refresh_statuses(
            [c['fileId'] for c in channels_list.values() if c.get('fileId')])
    shown = 0
    for asset in assets:
        series_id = _series_id(asset)
        if series_id:
            _render_series_folder(asset, series_id, label)
            shown += 1
        elif _render_asset(asset, channels_list, hide_unsubscribed, statuses):
            shown += 1
    xbmc.log('Vodafone TV > search "%s": %d assets, %d results'
             % (query, len(assets), shown), xbmc.LOGINFO)

    if shown == 0:
        xbmcgui.Dialog().notification('Vodafone TV','Nic nenalezeno', xbmcgui.NOTIFICATION_INFO, 3000)
        return
    xbmcplugin.endOfDirectory(_handle)


def list_series_episodes(series_id, name, label):
    """Every broadcast of one series -- the folder opened from a series result.

    Expands the series id the same way the web player does (get_series_epg): a
    KalturaSearchAssetFilter over the catch-up window. Aired episodes play from
    the archive; upcoming ones are listed so they can be recorded.
    """
    xbmcplugin.setPluginCategory(_handle, label)
    from libs.epg import get_series_epg
    try:
        episodes = get_series_epg(series_id)
    except Exception as e:
        xbmc.log('Vodafone TV > series expand failed: %s' % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Epizody se nepodařilo načíst',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    channels_list = Channels().get_channels_list('id', visible_filter = False)
    # Same rule as the archive/live listings: drop airings on channels the
    # household is not subscribed to when "hide unsubscribed" is on.
    hide_unsubscribed = entitlement.hide_enabled()
    statuses = {}
    if hide_unsubscribed:
        statuses = entitlement.refresh_statuses(
            [c['fileId'] for c in channels_list.values() if c.get('fileId')])
    now = int(time.time())
    shown = 0
    # Newest first: the most recent airings are the ones inside the catch-up
    # window and thus playable.
    for key in sorted(episodes.keys(), reverse = True):
        item = episodes[key]
        channel = channels_list.get(item['channel_id'])
        if hide_unsubscribed and (channel is None or entitlement.is_unentitled(
                item['channel_id'], channel.get('fileId'), statuses)):
            continue
        if _render_episode(item, channel, now, series_id, name, label):
            shown += 1
    xbmc.log('Vodafone TV > series "%s" (%s): %d episodes'
             % (name, series_id, shown), xbmc.LOGINFO)

    if shown == 0:
        xbmcgui.Dialog().notification('Vodafone TV', 'Nic nenalezeno', xbmcgui.NOTIFICATION_INFO, 3000)
        return
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


def _render_episode(item, channel, now, series_id, name, label):
    """One airing of a series. Aired episodes are playable archive items; an
    upcoming airing is listed (greyed) so it can still be recorded. ``channel``
    is the resolved channel dict for this airing, or None if not in the lineup."""
    channel_name = channel['name'] if channel else ''
    when = (day_translation_short[datetime.fromtimestamp(item['startts']).strftime('%w')] + ' '
            + datetime.fromtimestamp(item['startts']).strftime('%d.%m. %H:%M') + ' - '
            + datetime.fromtimestamp(item['endts']).strftime('%H:%M'))
    caption = when + (' | ' + channel_name if channel_name else '') + ' | ' + (item.get('title') or '')

    aired = int(item['endts']) <= now
    if not aired:
        caption = '[COLOR gray]%s (bude vysíláno)[/COLOR]' % caption
    list_item = xbmcgui.ListItem(label = caption)
    epg_listitem(list_item = list_item, epg = item, logo = (channel['logo'] if channel else ''))
    list_item.addContextMenuItems(
        [('Přidat nahrávku', 'RunPlugin(plugin://' + plugin_id + '?action=add_recording&id=' + str(item['id']) + ')')])
    list_item.setContentLookup(False)

    if aired:
        list_item.setProperty('IsPlayable', 'true')
        url = get_url(action = 'play_archive', id = item['id'], epg = json.dumps(item),
                      channel_id = item['channel_id'], startts = item['startts'], endts = item['endts'])
        xbmcplugin.addDirectoryItem(_handle, url, list_item, False)
    else:
        # Not playable yet -- point a stray click back at this folder so nothing
        # tries (and fails) to play a future programme; recording is via the menu.
        url = get_url(action = 'list_series_episodes', series_id = series_id, name = name, label = label)
        xbmcplugin.addDirectoryItem(_handle, url, list_item, True)
    return True


def _render_asset(asset, channels_list, hide_unsubscribed = False, statuses = None):
    """Render one search hit as a playable item: an archive programme if it is a
    linear broadcast, otherwise a Videotéka title. Returns True if shown.

    A linear hit on a channel the household is not subscribed to is dropped when
    "hide unsubscribed" is on, the same rule the archive and live listings use.
    """
    epg = programme_from_asset(asset, channels_list)
    if epg:
        channel = channels_list.get(epg['channel_id'])
        if hide_unsubscribed and (channel is None or entitlement.is_unentitled(
                epg['channel_id'], channel.get('fileId'), statuses or {})):
            return False
        _render_programme(epg, channels_list)
        return True
    return _render_vod_hit(asset)


def _meta(asset, *keys):
    """One metas value by any of the given key spellings, or None.

    Search hits use a flat metas shape (``metas['episode name']``); the Kaltura
    shape nests the value (``metas['SeriesName']['value']``). Handle both so the
    same helper works whichever the gateway returns.
    """
    metas = asset.get('metas') or {}
    for key in keys:
        value = metas.get(key)
        if isinstance(value, dict):
            value = value.get('value')
        if value not in (None, ''):
            return value
    return None


def _series_id(asset):
    """The series id a search hit belongs to, or None for a standalone title.

    The gateway (useCase=3) collapses a series to a single hit carrying the id in
    metas: ``unifiedSeriesID`` (the cross-channel id) or ``series ID`` (note the
    space and the two casings the backend uses). Films and one-off programmes
    carry none, so they stay inline.
    """
    return _meta(asset, 'unifiedSeriesID', 'series ID', 'Series ID', 'SeriesID')


def _episode_suffix(asset):
    """"SxxEyy"/"Eyy" from a hit's season/episode numbers, or '' if none/bad."""
    def as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    number = as_int(_meta(asset, 'EpisodeNumber', 'episode number', 'episode num'))
    if number is None:
        return ''
    season = as_int(_meta(asset, 'SeasonNumber', 'season number'))
    return 'S%02dE%02d' % (season, number) if season else 'E%02d' % number


def _asset_image(asset):
    for wanted in ('cc', '16:9', 'bg', 'ca'):
        candidates = [i for i in (asset.get('images') or []) if i.get('ratio') == wanted and i.get('url')]
        if candidates:
            return max(candidates, key = lambda i: i.get('width') or 0).get('url')
    return None


def _render_series_folder(asset, series_id, label):
    """A series search hit rendered as a folder that expands to its episodes."""
    name = asset.get('name') or 'Seriál'
    list_item = xbmcgui.ListItem(label = '%s »' % name)
    image = _asset_image(asset)
    if image:
        list_item.setArt({'poster': image, 'thumb': image, 'icon': image})
    info = {'title': name, 'mediatype': 'tvshow'}
    if asset.get('description'):
        info['plot'] = asset['description']
    list_item.setInfo('video', info)
    url = get_url(action = 'list_series_episodes', series_id = series_id,
                  name = name, label = label + ' / ' + name)
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)


def _render_programme(epg, channels_list):
    channel = channels_list[epg['channel_id']]
    list_item = xbmcgui.ListItem(label = epg['title'] + ' (' + channel['name'] + ' | '
        + day_translation_short[datetime.fromtimestamp(epg['startts']).strftime('%w')] + ' '
        + datetime.fromtimestamp(epg['startts']).strftime('%d.%m. %H:%M') + ' - '
        + datetime.fromtimestamp(epg['endts']).strftime('%H:%M') + ')')
    list_item = epg_listitem(list_item = list_item, epg = epg,
                             logo = epg.get('image') or channel['logo'])
    list_item.setProperty('IsPlayable', 'true')
    list_item.setContentLookup(False)
    url = get_url(action='play_archive', id = epg['id'], epg = json.dumps(epg),
                  channel_id = epg['channel_id'], startts = epg['startts'], endts = epg['endts'])
    menus = [('Přidat nahrávku', 'RunPlugin(plugin://' + plugin_id + '?action=add_recording&id=' + str(epg['id']) + ')')]
    list_item.addContextMenuItems(menus)
    xbmcplugin.addDirectoryItem(_handle, url, list_item, False)


def _render_vod_hit(asset):
    """Render a VOD search hit as a playable Videotéka item. Returns True if it
    was shown. Series/packages without a directly playable file are skipped
    here -- search surfaces individual titles, and those need their own browse.
    """
    from libs import vod
    # 736 film, 737 episode, 738 series, 740 package -- the VOD asset types the
    # search filter returns (see SEARCH filter_types). Anything else is linear.
    if asset.get('type') not in (vod.TYPE_MOVIE, 737, vod.TYPE_SERIES, vod.TYPE_PACKAGE):
        return False
    file_id = vod.pick_file_id(asset)
    if not file_id:
        return False

    metas = asset.get('metas') or {}
    image = ''
    for ratio in ('cc', '16:9', 'bg', 'ca'):
        for img in asset.get('images') or []:
            if img.get('ratio') == ratio and img.get('url'):
                image = img['url']
                break
        if image:
            break
    year = metas.get('Release year')
    epg = {'description': asset.get('description') or metas.get('Summary medium') or '',
           'cover': image, 'poster': image, 'year': str(year) if year else ''}

    # Inside a series folder the name repeats on every episode, so append the
    # episode label (name, or SxxEyy) to tell them apart.
    base = asset.get('name') or ''
    episode = _meta(asset, 'EpisodeName', 'episode name')
    if episode:
        base = '%s - %s' % (base, episode)
    else:
        suffix = _episode_suffix(asset)
        if suffix:
            base = '%s - %s' % (base, suffix)
    list_item = xbmcgui.ListItem(label = base + ' (Videotéka)')
    epg_listitem(list_item, epg, logo = image)
    list_item.setProperty('IsPlayable', 'true')
    list_item.setContentLookup(False)
    url = get_url(action='play_vod', asset_id = asset.get('id'), file_id = file_id)
    xbmcplugin.addDirectoryItem(_handle, url, list_item, False)
    return True

def save_search_history(query):
    addon = xbmcaddon.Addon()
    addon_userdata_dir = translatePath(addon.getAddonInfo('profile')) 
    max_history = int(addon.getSetting('search_history'))
    cnt = 0
    history = []
    filename = addon_userdata_dir + 'search_history.txt'
    try:
        with open(filename, 'r') as file:
            for line in file:
                item = line[:-1]
                history.append(item)
    except IOError:
        history = []
    history.insert(0,query)
    with open(filename, 'w') as file:
        for item  in history:
            cnt = cnt + 1
            if cnt <= max_history:
                file.write('%s\n' % item)

def load_search_history():
    history = []
    addon = xbmcaddon.Addon()
    addon_userdata_dir = translatePath(addon.getAddonInfo('profile')) 
    filename = addon_userdata_dir + 'search_history.txt'
    try:
        with open(filename, 'r') as file:
            for line in file:
                item = line[:-1]
                history.append(item)
    except IOError:
        history = []
    return history

def delete_search(query):
    addon = xbmcaddon.Addon()
    addon_userdata_dir = translatePath(addon.getAddonInfo('profile')) 
    filename = addon_userdata_dir + 'search_history.txt'
    history = load_search_history()
    for item in history:
        if item == query:
            history.remove(item)
    try:
        with open(filename, 'w') as file:
            for item in history:
                file.write('%s\n' % item)
    except IOError:
        pass
    xbmc.executebuiltin('Container.Refresh')


# -*- coding: utf-8 -*-
"""Video on demand -- the films/series library the web player calls "Videotéka".

The web app builds the whole thing from three calls the addon already speaks:

  * GetMenu (tvapi gateway) returns the menu tree. Its FILMS and FOLDERS items
    carry Children whose ``URL`` is a Kaltura category id -- one per rail/genre.
  * asset/list with a KalturaChannelFilter(idEqual=<category id>) returns the
    titles in that rail. Each title is a Kaltura asset:
        type 736 -- a film/episode: has mediaFiles, so it is playable.
        type 738 -- a series container: no files; drilled into by Series ID.
        type 740 -- a package/bundle (e.g. "Filmbox OD"): its ``channels`` meta
                    is another category id, so it drills in like any rail.
  * getPlaybackManifest with assetType=media / context=PLAYBACK -- exactly the
    live path, so playback, DRM and the session slot are all reused as-is
    (see stream.play_vod).

No response bodies were needed beyond confirming these shapes: the asset object
is the same Kaltura shape epg.py already parses.
"""
import sys
import xbmc
import xbmcgui
import xbmcplugin

from libs.utils import get_url, clientTag, apiVersion
from libs.session import Session
from libs.epg import epg_listitem
from libs.api import API
from libs import tvapi

_handle = int(sys.argv[1])

# The root menu id the web player asks GetMenu for, and the two top-level items
# that hold the VOD catalogue.
MENU_ROOT_ID = 100000008
FILMS_MENU_ID = 100000524      # "FILMS" -- the editorial rails
FOLDERS_MENU_ID = 100000531    # "FOLDERS" -- the genre catalogue

# Kaltura asset type ids used by the VOD catalogue.
TYPE_MOVIE = 736
TYPE_SERIES = 738
TYPE_PACKAGE = 740

# The web player plays the DASH_AVC_FULLHD_HTTPS media file; that is the very
# source type stream.play_stream picks out of the manifest. Fall back to the
# other DASH variants for titles that lack the preferred one.
PREFERRED_FILE_TYPES = ('DASH_AVC_FULLHD_HTTPS', 'DASH_AVC_FULLHD',
                        'DASH_AVC_SD_HTTPS', 'DASH_AVC_SD')

PAGE_SIZE = 100

# The same Kaltura asset/list endpoint the EPG uses (ks in the body, no HMAC).
ASSET_LIST_URL = 'https://3062.vfp2.ott.kaltura.com/api_v3/service/asset/action/list'


def asset_list_page(asset_filter, page_index, page_size = PAGE_SIZE):
    """One page of asset/list. Returns (objects, total_count).

    Unlike libs.api.list_api -- which loops until it has *every* object -- this
    fetches a single page so large categories (e.g. "Všechny filmy", thousands
    of titles) paginate in the UI instead of being pulled down all at once.
    """
    post = {'language': 'ces', 'ks': Session().ks, 'filter': asset_filter,
            'pager': {'objectType': 'KalturaFilterPager',
                      'pageSize': page_size, 'pageIndex': page_index},
            'clientTag': clientTag, 'apiVersion': apiVersion}
    api = API()
    data = api.call_api(url = ASSET_LIST_URL, data = post, headers = api.headers, nolog = True)
    result = data.get('result') if isinstance(data, dict) else None
    if not result or 'objects' not in result:
        return [], 0
    return result['objects'], result.get('totalCount', 0)


# ---------------------------------------------------------------------------
# menu
# ---------------------------------------------------------------------------

def get_menu_children(menu_id):
    """The Children of one GetMenu item, or [] -- fetched live from the gateway.

    GetMenu returns the whole tree under MenuItems; the item we want (FILMS,
    FOLDERS...) is found by ID and its Children are the rails/categories, each a
    dict with Name and a numeric URL (the Kaltura category id).
    """
    session = Session()
    try:
        data = tvapi.call(session, 'GetMenu', {'id': MENU_ROOT_ID}, log_response = False)
    except Exception as error:
        xbmc.log('Vodafone TV > GetMenu failed: %s' % error, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Nepodařilo se načíst nabídku videotéky',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return []

    def find(items):
        for item in items or []:
            if item.get('ID') == menu_id:
                return item.get('Children') or []
            hit = find(item.get('Children'))
            if hit:
                return hit
        return []

    return find((data or {}).get('MenuItems'))


def menu_categories(menu_id):
    """(name, category_id) for each child of a menu item with a numeric URL.

    Non-catalogue children (SET_VALUES, External|..., ".") carry a non-numeric
    URL and are skipped -- those are recommendation rails and filters, not
    browsable categories.
    """
    out = []
    for child in get_menu_children(menu_id):
        url = str(child.get('URL') or '').strip()
        if url.isdigit():
            out.append((child.get('Name') or '', int(url)))
    return out


def list_vod(label):
    """Top level of the VOD section: the FILMS rails plus a Genres folder."""
    xbmcplugin.setPluginCategory(_handle, label)
    for name, category_id in menu_categories(FILMS_MENU_ID):
        _add_dir(name, get_url(action = 'vod_category', id = category_id,
                               label = label + ' / ' + name))
    # The genre catalogue (FOLDERS) is large; keep it one level down.
    _add_dir('Žánry', get_url(action = 'vod_folders', label = label + ' / Žánry'))
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


def list_vod_folders(label):
    """The FOLDERS genre catalogue."""
    xbmcplugin.setPluginCategory(_handle, label)
    for name, category_id in menu_categories(FOLDERS_MENU_ID):
        _add_dir(name, get_url(action = 'vod_category', id = category_id,
                               label = label + ' / ' + name))
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


# ---------------------------------------------------------------------------
# listing titles
# ---------------------------------------------------------------------------

def list_vod_category(category_id, label, page = 1):
    """The titles in one rail/category (a Kaltura channel), one page at a time."""
    page = int(page)
    xbmcplugin.setPluginCategory(_handle, label)
    objects, total = asset_list_page(
        {'objectType': 'KalturaChannelFilter', 'idEqual': int(category_id)}, page)
    _render_assets(objects, label)
    _add_next_page(total, page, get_url(action = 'vod_category', id = category_id,
                                        label = label, page = page + 1))
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


def list_vod_series(series_id, label, page = 1):
    """The episodes of a series, by its Series ID.

    No series click was captured, so the exact filter is best-effort: a
    KalturaSearchAssetFilter on the Series ID the series asset carries. If a
    given deployment names the field differently the list comes back empty and
    the user is told, rather than the addon failing.
    """
    page = int(page)
    xbmcplugin.setPluginCategory(_handle, label)
    ksql = "(and SeriesId='%s')" % series_id
    objects, total = asset_list_page(
        {'objectType': 'KalturaSearchAssetFilter', 'kSql': ksql,
         'orderBy': 'START_DATE_ASC', 'typeIn': '%d' % TYPE_MOVIE}, page)
    count = _render_assets(objects, label)
    if count == 0 and page == 1:
        xbmcgui.Dialog().notification('Vodafone TV', 'Epizody se nepodařilo načíst',
                                      xbmcgui.NOTIFICATION_INFO, 4000)
    _add_next_page(total, page, get_url(action = 'vod_series', series_id = series_id,
                                        label = label, page = page + 1))
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)


def _add_next_page(total, page, url):
    """A "Next page" folder item when more titles remain beyond this page."""
    if total > page * PAGE_SIZE:
        _add_dir('Další stránka (%d) →' % (page + 1), url)


def _render_assets(assets, label):
    """Turn a list of Kaltura assets into directory items. Returns how many."""
    shown = 0
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_type = asset.get('type')
        name = asset.get('name') or ''
        list_item = xbmcgui.ListItem(label = name)
        _decorate(list_item, asset)

        if asset_type == TYPE_SERIES:
            series_id = _series_id(asset)
            if not series_id:
                continue
            url = get_url(action = 'vod_series', series_id = series_id,
                          label = label + ' / ' + name)
            xbmcplugin.addDirectoryItem(_handle, url, list_item, True)
            shown += 1
        elif asset_type == TYPE_PACKAGE:
            channel = _meta_value(asset, 'channels')
            if not channel or not str(channel).isdigit():
                continue
            url = get_url(action = 'vod_category', id = int(channel),
                          label = label + ' / ' + name)
            xbmcplugin.addDirectoryItem(_handle, url, list_item, True)
            shown += 1
        else:
            file_id = pick_file_id(asset)
            if not file_id:
                continue
            list_item.setProperty('IsPlayable', 'true')
            list_item.setContentLookup(False)
            url = get_url(action = 'play_vod', asset_id = asset.get('id'), file_id = file_id)
            xbmcplugin.addDirectoryItem(_handle, url, list_item, False)
            shown += 1
    return shown


# ---------------------------------------------------------------------------
# asset helpers
# ---------------------------------------------------------------------------

def pick_file_id(asset):
    """The media file id to play -- the preferred DASH type, else None.

    Handles both file shapes: the phoenix asset/list `mediaFiles` and the
    external/search asset `files` (both carry id + type).
    """
    by_type = {}
    for media_file in asset.get('mediaFiles') or asset.get('files') or []:
        if media_file.get('id') and media_file.get('type'):
            by_type.setdefault(media_file['type'], media_file['id'])
    for wanted in PREFERRED_FILE_TYPES:
        if wanted in by_type:
            return by_type[wanted]
    return None


def _meta_value(asset, name):
    meta = (asset.get('metas') or {}).get(name)
    if isinstance(meta, dict):
        return meta.get('value')
    return meta


def _tag_values(asset, name):
    tag = (asset.get('tags') or {}).get(name)
    if isinstance(tag, dict):
        if 'objects' in tag:
            return [o.get('value') for o in tag['objects'] if o.get('value')]
        if tag.get('value'):
            return [tag['value']]
    return []


def _series_id(asset):
    values = _tag_values(asset, 'Series ID')
    return values[0] if values else None


def _image_url(asset, ratios):
    images = asset.get('images') or []
    for ratio in ratios:
        for image in images:
            if image.get('ratio') == ratio and image.get('url'):
                return image['url']
    return ''


def _decorate(list_item, asset):
    """Set art and video info from a VOD asset, via the shared epg_listitem.

    VOD assets carry the same information as programmes under slightly different
    key names (Release year, Main cast, Series Name...); map them into the dict
    epg_listitem already knows how to render so the metadata looks the same
    everywhere.
    """
    year = _meta_value(asset, 'Release year')
    epg = {
        'description': asset.get('description') or _meta_value(asset, 'Summary medium') or '',
        'poster': _image_url(asset, ('ca', 'cc', '16:9', 'bg')),
        'cover': _image_url(asset, ('16:9', 'bg', 'cc', 'ca')),
        'year': str(year) if year else '',
        'genres': _tag_values(asset, 'Genre') or _tag_values(asset, 'genre_desc'),
        'cast': [(name, '') for name in _tag_values(asset, 'Main cast')],
        'directors': _tag_values(asset, 'Director'),
        'country': (_tag_values(asset, 'Country') or [''])[0],
        # NB: don't set 'seriesName' here -- epg_listitem couples it to
        # 'seasonNumber' (addSeason(int(seasonNumber), seriesName)), which VOD
        # assets don't carry, and the missing key raises KeyError.
    }
    epg_listitem(list_item, epg, logo = epg['cover'] or epg['poster'])


def _add_dir(label, url):
    xbmcplugin.addDirectoryItem(_handle, url, xbmcgui.ListItem(label = label), True)

# -*- coding: utf-8 -*-
import sys
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

import ssl
import json
import time
import base64
from xml.dom import minidom
from urllib.request import urlopen, Request
from urllib.parse import quote

from datetime import datetime, timezone

from libs.session import Session, recover_expired, UNREGISTERED_NOTIFICATION
from libs.channels import Channels
from libs.api import API, is_ks_error
from libs.epg import get_channel_epg, get_channel_live_epg
from libs.utils import apiVersion, get_kodi_version
from libs import widevine
from libs import entitlement
from libs import dms

ADDON = xbmcaddon.Addon()
# Get stored port, default to 8080 if not set
PORT = ADDON.getSetting('proxy_port') or '8080'
BASE_URL = f'http://127.0.0.1:{PORT}/'

if len(sys.argv) > 1:
    _handle = int(sys.argv[1])

def get_stream_url(asset_id, program_id, file_id, manifest_url):
    """Generate URLs for manifest and license"""
    manifest_url_encoded = quote(manifest_url, safe='')
    manifest_url = f'{BASE_URL}manifest?url={manifest_url_encoded}'
    license_url = f'{BASE_URL}license?asset_id={asset_id}&program_id={program_id}&file_id={file_id}'
    return manifest_url, license_url


# def play_catchup(id, start_ts, end_ts):
#     start_ts = int(start_ts)
#     end_ts = int(end_ts)
#     epg = get_channel_epg(id = id, from_ts = start_ts, to_ts = end_ts + 60*60*12)
#     if start_ts in epg:
#         if epg[start_ts]['endts'] > int(time.mktime(datetime.now().timetuple()))-10:
#             play_startover(id = epg[start_ts]['id'], channel_id = id)
#         else:
#             play_archive(id = epg[start_ts]['id'], epg = epg[start_ts], channel_id = id, startts = epg[start_ts]['startts'], endts = epg[start_ts]['endts'])
#     else:
#         play_live(id, epg[id])

# def play_startover(id, channel_id):
#     session = Session()
#     post = {"1":{"service":"asset","action":"get","id":id,"assetReferenceType":"epg_internal","ks":session.ks},"2":{"service":"asset","action":"getPlaybackContext","assetId":id,"assetType":"epg","contextDataParams":{"objectType":"KalturaPlaybackContextOptions","context":"START_OVER","streamerType":"mpegdash","urlType":"DIRECT"},"ks":session.ks},"apiVersion":"7.8.1","ks":session.ks,"partnerId":partnerId}    
#     play_stream(post, channel_id)
def play_live(id, mode = None):
    """Play a live channel. `mode` overrides the default from the settings."""
    mode = mode or default_live_mode()

    session = Session()
    asset_id = int(id)
    channels_list = Channels().get_channels_list('id')
    channel = channels_list.get(asset_id)
    if channel is None:
        xbmcgui.Dialog().notification('Vodafone TV', 'Kanál nebyl nalezen',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    # The current programme gives the CATCHUP window (for rewind) and the
    # programId the DRM bookmark needs. Ask for just this channel -- the
    # all-channels get_live_epg is a large response that times out on a slow
    # connection and takes playback down with it.
    try:
        epg = get_channel_live_epg(asset_id) or {}
    except Exception as error:
        xbmc.log('Vodafone TV > current programme lookup failed: %s' % error,
                 xbmc.LOGWARNING)
        epg = {}
    if not epg.get('id'):
        xbmcgui.Dialog().notification('Vodafone TV',
                                      'Nepodařilo se načíst aktuální pořad, zkus to znovu',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    post = {"assetId":id,"assetType":"media","contextDataParams":{"objectType":"KalturaPlaybackContextOptions","assetFileIds":channel.get('fileId'),"context":"PLAYBACK","urlType":"DIRECT"},"apiVersion":apiVersion,"ks":session.ks}

    # With rewind enabled, play the CATCHUP manifest starting at the current
    # show's start (so seeking back reaches the start of the show). The window
    # end is pushed well past "now" -- not just to the show's end -- so the
    # manifest keeps growing to the live edge across programme boundaries
    # instead of freezing when the next show begins. Falls back to the plain
    # live manifest if the stretched window is refused.
    if mode == CATCHUP_MODE and epg.get('startts') and epg.get('endts'):
        end_ts = int(time.time()) + LIVE_CATCHUP_AHEAD
        catchup = catchup_post(session, epg.get('id'), channel.get('fileId'),
                               epg.get('startts'), end_ts)
        play_stream(session, catchup, asset_id, epg.get('id'),
                    channel.get('fileId'), fallback_post = post)
    else:
        play_stream(session, post, asset_id, epg.get('id'), channel.get('fileId'))


def play_vod(asset_id, file_id):
    """Play an on-demand title (a Kaltura media asset).

    The same playback context the web player sends for VOD: assetType `media`,
    context `PLAYBACK`, with the chosen media file as `assetFileIds`. Unlike
    live there is no channel and no programme -- the media asset is the thing
    itself -- so program_id is None; the license/bookmark batch then carries no
    programId, which is exactly the web player's VOD bookmark.

    The manifest is static, so play from the start (play_timeshift_buffer is
    harmless there) rather than applying the live-edge delay.
    """
    session = Session()
    post = {'assetId': str(asset_id), 'assetType': 'media',
            'contextDataParams': {'objectType': 'KalturaPlaybackContextOptions',
                                  'assetFileIds': str(file_id),
                                  'context': 'PLAYBACK', 'urlType': 'DIRECT'},
            'apiVersion': apiVersion, 'ks': session.ks}
    play_stream(session, post, int(asset_id), None, int(file_id), from_start = True)


CATCHUP_MODE = 'catchup'
LIVE_MODE = 'live'

# Default seconds to sit behind the live edge (overridable in settings).
# Vodafone's packager generates live segments on demand and is sometimes a few
# seconds late; playing right at the edge makes ISA ask for a segment that is
# not ready yet, curl times out (~10 s) and the stream freezes. A small delay
# keeps the requested segment warm, at the cost of being that far behind live.
LIVE_DELAY = 20

# In catch-up (rewindable) live mode the CATCHUP manifest window ends at the
# current programme's end, so playback freezes when the next show starts. Extend
# the window's end this far past "now" so it rolls on across programme
# boundaries; the manifest still only exposes segments up to the live edge, so a
# larger value costs nothing but a later outer bound. If the server refuses the
# stretched window, play_stream falls back to the plain PLAYBACK manifest.
LIVE_CATCHUP_AHEAD = 8 * 60 * 60


def live_delay():
    """How many seconds behind the live edge to play (setting `live_delay`)."""
    try:
        return int(ADDON.getSetting('live_delay'))
    except (ValueError, TypeError):
        return LIVE_DELAY


def default_live_mode():
    """How a channel plays when opened without an explicit mode -- i.e. from the
    exported M3U in a PVR client (setting `live_mode`, default catchup).

    `catchup` uses the CATCHUP manifest of the programme that is on now, which
    is still live but reaches back to the start of the show; `live` uses the
    plain PLAYBACK manifest, which is the live edge with almost no rewind.
    """
    mode = (ADDON.getSetting('live_mode') or '').strip()
    return LIVE_MODE if mode == LIVE_MODE else CATCHUP_MODE


def addon_live_mode():
    """How clicking a channel in the addon's own live list plays it (setting
    `addon_live_mode`, default live). Separate from `live_mode` so the PVR feed
    can default to catch-up while the addon plays live."""
    mode = (ADDON.getSetting('addon_live_mode') or '').strip()
    return CATCHUP_MODE if mode == CATCHUP_MODE else LIVE_MODE


def utc_timestamp(ts):
    """The 2026-07-23T00:20:00Z form the CATCHUP adapterData wants."""
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def catchup_post(session, program_id, file_id, start_ts, end_ts):
    """A CATCHUP playback context -- the manifest that covers a whole show.

    The live PLAYBACK manifest only carries a very short timeShiftBufferDepth,
    so seeking back inside it gets nowhere. The web player asks for a second
    manifest instead: assetType `epg`, context `CATCHUP`, and the programme's
    start/end in adapterData. That one spans the show, so it can be played
    from the beginning.
    """
    return {
        'assetId': str(program_id),
        'assetType': 'epg',
        'contextDataParams': {
            'objectType': 'KalturaPlaybackContextOptions',
            'assetFileIds': str(file_id),
            'context': 'CATCHUP',
            'urlType': 'DIRECT',
            'adapterData': {
                'manifestStartTime': {'objectType': 'KalturaStringValue',
                                      'value': utc_timestamp(start_ts)},
                'manifestEndTime': {'objectType': 'KalturaStringValue',
                                    'value': utc_timestamp(end_ts)},
            },
        },
        'apiVersion': apiVersion,
        'ks': session.ks,
    }


def play_archive(id, epg = None, channel_id = None, startts = None, endts = None):
    """Play a programme from the archive.

    The same CATCHUP manifest the start-over path uses -- the captured browser
    requests for past programmes (libs/1.json, libs/2.json) are exactly this,
    assetType `epg` with the programme's window in adapterData. The bookmark in
    the license batch still carries the channel as its id and the programme as
    programId, which is what those captures do too.
    """
    if isinstance(epg, str):
        epg = json.loads(epg)
    epg = epg or {}

    if startts is None or endts is None:
        startts, endts = epg.get('startts'), epg.get('endts')
    if channel_id is None:
        channel_id = epg.get('channel_id')
    if not (id and channel_id and startts and endts):
        xbmcgui.Dialog().notification('Vodafone TV', 'Chybí údaje o pořadu',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    channels_list = Channels().get_channels_list('id', visible_filter = False)
    channel = channels_list.get(int(channel_id))
    if channel is None:
        xbmcgui.Dialog().notification('Vodafone TV', 'Kanál nebyl nalezen',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    session = Session()
    file_id = channel.get('fileId')
    post = catchup_post(session, id, file_id, startts, endts)
    xbmc.log('Vodafone TV > archive %s (%s) from %s to %s'
             % (epg.get('title', '?'), id,
                post['contextDataParams']['adapterData']['manifestStartTime']['value'],
                post['contextDataParams']['adapterData']['manifestEndTime']['value']),
             xbmc.LOGINFO)
    play_stream(session, post, int(channel_id), id, file_id, from_start = True)


def play_recording(channel_id, startts, endts):
    """Play a completed recording.

    A recording carries no Kaltura asset id -- only the channel's external id
    and its window -- so find the programme in that channel's EPG and play it
    like any other archive item. (Whether a recording has a stream of its own
    is not captured anywhere; this uses catch-up, which covers the same period
    for a channel that has it.)
    """
    startts, endts = int(startts), int(endts)
    epg = get_channel_epg(int(channel_id), startts - 1800, endts + 1800)

    programme = None
    for item in epg.values():
        # the recording window is padded, so match the closest start
        if abs(int(item.get('startts', 0)) - startts) <= 300:
            programme = item
            break
    if programme is None:
        xbmcgui.Dialog().notification('Vodafone TV', 'Pořad nebyl v EPG nalezen',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    play_archive(programme['id'], programme, channel_id,
                 programme['startts'], programme['endts'])


def play_catchup(channel_id, start, end):
    """Play a past programme picked from the guide in a PVR client (catch-up).

    IPTV Simple fills the chosen programme's start/end into the M3U
    ``catchup-source`` and plays that URL. We are handed the channel id and the
    time window but not the Kaltura programme id, so the programme is looked up
    in that channel's EPG (the one whose start is nearest the requested start,
    which absorbs any begin/end buffer IPTV Simple adds) and played from the
    archive.
    """
    start, end = int(start), int(end)
    epg = get_channel_epg(int(channel_id), start - 3600, end + 3600)
    if not epg:
        xbmcgui.Dialog().notification('Vodafone TV', 'Pořad nebyl v EPG nalezen',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    programme = min(epg.values(),
                    key=lambda item: abs(int(item.get('startts', 0)) - start))
    play_archive(programme['id'], programme, channel_id,
                 programme['startts'], programme['endts'])


def play_startover(id):
    """Play the programme currently on `id` from its beginning."""
    session = Session()
    try:
        epg = get_channel_live_epg(int(id))
    except Exception as error:
        xbmc.log('Vodafone TV > current programme lookup failed: %s' % error,
                 xbmc.LOGWARNING)
        epg = None
    channels_list = Channels().get_channels_list('id')
    asset_id = int(id)

    if not epg or asset_id not in channels_list:
        xbmcgui.Dialog().notification('Vodafone TV', 'Chybí EPG pro tento kanál',
                                      xbmcgui.NOTIFICATION_ERROR, 5000)
        return

    channel = channels_list[asset_id]
    file_id = channel.get('fileId')
    post = catchup_post(session, epg.get('id'), file_id,
                        epg.get('startts'), epg.get('endts'))
    xbmc.log('Vodafone TV > start over %s from %s to %s'
             % (epg.get('title'), post['contextDataParams']['adapterData']['manifestStartTime']['value'],
                post['contextDataParams']['adapterData']['manifestEndTime']['value']), xbmc.LOGINFO)
    play_stream(session, post, asset_id, epg.get('id'), file_id, from_start = True)

# def play_archive(id, epg, channel_id, startts, endts):
#     session = Session()
#     no_remove = False
#     if epg['md'] is not None:
#         items = []
#         ids = []
#         post = {"language":"ces","ks":session.ks,"filter":{"objectType":"KalturaSearchAssetFilter","orderBy":"START_DATE_ASC","kSql":"(and IsMosaicEvent='1' MosaicInfo='mosaic' (or externalId='" + str(epg['md']) + "'))"},"pager":{"objectType":"KalturaFilterPager","pageSize":200,"pageIndex":1},"clientTag":clientTag,"apiVersion":apiVersion}
#         for md_epg_item in md_epg:
#             md_ids = []
#             if 'MosaicChannelsInfo' in md_epg_item['tags']:
#                 for mditem in md_epg_item['tags']['MosaicChannelsInfo']['objects']:
#                     if 'ProgramExternalID' in mditem['value']:
#                         md_ids.append(mditem['value'].split('ProgramExternalID=')[1])
#                 for md_id in md_ids:
#                     post = {"language":"ces","ks":session.ks,"filter":{"objectType":"KalturaSearchAssetFilter","orderBy":"START_DATE_ASC","kSql":"(or externalId='" + str(md_id) + "')"},"pager":{"objectType":"KalturaFilterPager","pageSize":200,"pageIndex":1},"clientTag":clientTag,"apiVersion":apiVersion}
#                     if len(epg) > 0:
#                         item = epg[0]
#                         items.append(item['name'])
#                         ids.append(item['id'])
#         if len(items) > 0:
#             response = xbmcgui.Dialog().select(heading = 'Multidimenze - výběr streamu', list = items, preselect = 0)
#             if response < 0:
#                 response = 0
#             id = ids[response]

#     # post = {"1":{"service":"asset","action":"get","id":id,"assetReferenceType":"epg_internal","ks":session.ks},"2":{"service":"asset","action":"getPlaybackContext","assetId":id,"assetType":"epg","contextDataParams":{"objectType":"KalturaPlaybackContextOptions","context":"START_OVER","streamerType":"mpegdash","urlType":"DIRECT"},"ks":session.ks},"apiVersion":"7.8.1","ks":session.ks,"partnerId":partnerId}    
#     # play_stream(post)

#     post = {"language":"ces","ks":session.ks,"responseProfile":{"objectType":"KalturaOnDemandResponseProfile","relatedProfiles":[{"objectType":"KalturaDetachedResponseProfile","name":"group_result","filter":{"objectType":"KalturaAggregationCountFilter"}}]},"filter":{"objectType":"KalturaSearchAssetFilter","orderBy":"START_DATE_DESC","kSql":"(and asset_type='recording' start_date <'0' end_date < '-900')","groupBy":[{"objectType":"KalturaAssetMetaOrTagGroupBy","value":"SeriesID"}],"groupingOptionEqual":"Include"},"pager":{"objectType":"KalturaFilterPager","pageSize":500,"pageIndex":1},"clientTag":clientTag,"apiVersion":apiVersion}
#     for item in result:
#         if int(item['id']) == int(id):
#             no_remove = True
#     post = {"language":"ces","ks":session.ks,"recording":{"objectType":"KalturaRecording","assetId":id},"clientTag":clientTag,"apiVersion":apiVersion}
#     if 'err' in data or not 'result' in data or not 'status' in data['result'] or data['result']['status'] != 'RECORDED':
#         post = {"1":{"service":"asset","action":"get","id":id,"assetReferenceType":"epg_internal","ks":session.ks},"2":{"service":"asset","action":"getPlaybackContext","assetId":id,"assetType":"epg","contextDataParams":{"objectType":"KalturaPlaybackContextOptions","context":"CATCHUP","streamerType":"mpegdash","urlType":"DIRECT"},"ks":session.ks},"apiVersion":"7.8.1","ks":session.ks,"partnerId":partnerId}
#         play_stream(post, channel_id)
#     else:
#         recording_id = data['result']['id']
#         play_recording(recording_id, channel_id)
#         if no_remove == False:
#             post = {"language":"ces","ks":session.ks,"id":recording_id,"clientTag":clientTag,"apiVersion":apiVersion}
            
# def play_recording(id, channel_id):
#     session = Session()
#     post = {"1":{"service":"asset","action":"get","id":id,"assetReferenceType":"npvr","ks":session.ks},"2":{"service":"asset","action":"getPlaybackContext","assetId":id,"assetType":"recording","contextDataParams":{"objectType":"KalturaPlaybackContextOptions","context":"PLAYBACK","streamerType":"mpegdash","urlType":"DIRECT"},"ks":session.ks},"apiVersion":"7.8.1","ks":session.ks,"partnerId":partnerId}
#     play_stream(post, channel_id)

def start_license_session(session:Session, asset_id, program_id, file_id):
    return {
        "requests": [
            {
                "action": "bookmark",
                "params": {},
                "body": {
                    "bookmark": {
                        "objectType": "KalturaBookmark",
                        "id": asset_id,
                        "type": "media",
                        "programId": int(program_id),
                        "position": 0,
                        "playerData": {
                            "objectType": "KalturaBookmarkPlayerData",
                            "fileId": int(file_id),
                            "action": "PLAY"
                        }
                    },
                    "apiVersion": apiVersion,
                    "ks": session.ks
                }
            },
            {
                "action": "session",
                "params": {},
                "body": {}
            }
        ],
        "apiVersion": apiVersion,
        "ks": session.ks
    }

def set_clearkey_properties(list_item, keys):
    """Hand ISA the content keys we decrypted ourselves ({kid_hex: key_hex}).

    The JSON `inputstream.adaptive.drm` property only exists from Kodi 22 / ISA
    22 on; on Kodi 21 it is ignored without a word (ISA logs every property it
    recognises, and it never logged this one), leaving the stream with no
    decrypter at all -- "InitializePeriod: Unhandled encrypted stream". The v21
    way is `drm_legacy`.
    """
    if get_kodi_version() >= 22:
        drm_config = {'org.w3.clearkey': {'license': {'keyids': keys}}}
        list_item.setProperty('inputstream.adaptive.drm', json.dumps(drm_config))
    else:
        list_item.setProperty(
            'inputstream.adaptive.drm_legacy',
            'org.w3.clearkey|' + ','.join('%s:%s' % kv for kv in keys.items()))


def set_widevine_properties(list_item, license_url, license_cert):
    """Let ISA run Widevine against our license proxy (no privacy mode)."""
    list_item.setProperty('inputstream.adaptive.license_type', 'com.widevine.alpha')
    if get_kodi_version() >= 22:
        drm_config = {
            "com.widevine.alpha": {
                "priority": 1,
                "license": {
                    "server_url": license_url,
                    "req_data": base64.b64encode(b'{CHA-B64}!{SID-B64}').decode('utf-8'),
                    "server_certificate": license_cert
                },
                #"pre_init_data": 'AAAANHBzc2gAAAAA7e+LqXnWSs6jyCfc1R0h7QAAABQIARIQAAAAAAPSZ0kAAAAAAAAAAA==|AAAAAAPSZ0kAAAAAAAAAAA=='
            }
        }
        list_item.setProperty('inputstream.adaptive.drm', json.dumps(drm_config))
    else:
        list_item.setProperty('inputstream.adaptive.license_key', license_url + '||b{SSM}!b{SID}|')
        list_item.setProperty('inputstream.adaptive.license_flags', 'persistent_storage')
        list_item.setProperty('inputstream.adaptive.server_certificate', license_cert)
        #list_item.setProperty('inputstream.adaptive.pre_init_data', 'AAAANHBzc2gAAAAA7e+LqXnWSs6jyCfc1R0h7QAAABQIARIQAAAAAAPSZ0kAAAAAAAAAAA==|AAAAAAPSZ0kAAAAAAAAAAA==')
        # list_item.setProperty('inputstream.adaptive.manifest_type', 'mpd')


def get_playback_manifest(session, api, post):
    headers = api.headers.copy()
    req_body = session.sign(headers, post)
    return api.call_api(url = 'https://apigw.cz.vtv.vodafone.com/vtv/phoenix/v1/api_v3/service/asset/action/getPlaybackManifest', data = req_body, headers = headers)


def manifest_failed(data):
    return ('err' in data or not 'result' in data
            or not 'sources' in data['result']
            or len(data['result']['sources']) == 0)


def playback_error(data = None):
    """Say why playback did not start; be specific about a refused session."""
    if data is not None and is_ks_error(data):
        # We got here with a ks the service rejects and could not replace --
        # the device is unregistered, or the user declined to sign in again.
        message = UNREGISTERED_NOTIFICATION
    else:
        message = 'Problém při přehrání'
    xbmcgui.Dialog().notification('Vodafone TV', message,
                                  xbmcgui.NOTIFICATION_ERROR, 5000)


def renew_session(session, data, *posts):
    """Sign in again when the manifest was refused for a stale session.

    On success the posts are re-stamped with the new ks -- retrying with the
    old one would be refused exactly the same way.
    """
    if recover_expired(data, session) is None:
        return False
    for post in posts:
        if isinstance(post, dict) and 'ks' in post:
            post['ks'] = session.ks
    return True


def play_stream(session: Session, post, asset_id, program_id, file_id, fallback_post = None, from_start = False):
    api = API()

    err = False
    if asset_id is not None:
        data = get_playback_manifest(session, api, post)
        if manifest_failed(data) and renew_session(session, data, post, fallback_post):
            data = get_playback_manifest(session, api, post)
        if manifest_failed(data) and fallback_post is not None:
            # e.g. CATCHUP refused for this programme -- fall back to plain live
            widevine.log('CATCHUP manifest unavailable, falling back to live',
                         xbmc.LOGWARNING)
            post = fallback_post
            data = get_playback_manifest(session, api, post)
        if 'err' in data or not 'result' in data or not 'sources' in data['result']:
            playback_error(data)
        else:
            if len(data['result']['sources']) > 0:
                urls = {}
                for stream in data['result']['sources']:
                    license = None
                    for drm in stream['drm']:
                        if drm['scheme'] == 'WIDEVINE_CENC':
                            license = drm['licenseURL']
                    urls.update({stream['type'] : { 'url' : stream['url'], 'license' : license}})

                # Prefer FullHD (what live always carries); fall back to the SD
                # HTTPS source, which is all some VOD titles offer.
                source = None
                for _type in ('DASH_AVC_FULLHD_HTTPS', 'DASH_AVC_SD_HTTPS'):
                    if _type in urls:
                        source = urls[_type]
                        break
                if source is not None:
                    url, license_url = get_stream_url(asset_id, program_id, file_id, source['url'])

                    # No bookSlot here any more: one slot permits exactly one
                    # /start, so whoever makes the start call books it -- see
                    # widevine.book_slot() and the license proxy in proxy.py.
                    context=ssl.create_default_context()
                    context = ssl._create_unverified_context()
                    context.set_ciphers('DEFAULT')
                    request = Request(url = dms.nagra_certificate_url('https://vdcr01h5.anycast.nagra.com/VDCR01H5/wvls/contentlicenseservice/v1/certificates'), data = None)
                    response = urlopen(request)
                    license_cert = base64.b64encode(response.read()).decode('utf-8')

                    # Optional service-certificate override (ISA #1850): drop a
                    # base64 Widevine service cert into libs/service_cert.b64 to
                    # force ISA to use it (e.g. the common privacy cert used by the
                    # browser, provider_id=license.widevine.com). If absent, the
                    # Nagra-fetched cert above is used.
                    try:
                        import os
                        override_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'service_cert.b64')
                        if os.path.exists(override_path):
                            with open(override_path, 'r') as _f:
                                license_cert = _f.read().strip()
                            xbmc.log('Vodafone TV WV > using OVERRIDE service cert from service_cert.b64', xbmc.LOGINFO)
                    except Exception as _e:
                        xbmc.log('Vodafone TV WV > service cert override failed: %s' % _e, xbmc.LOGWARNING)
                    # mpd = response.geturl()
                    list_item = xbmcgui.ListItem(path = url)
                    list_item.setProperty('inputstream', 'inputstream.adaptive')

                    # Preferred path: do the Widevine exchange ourselves with a
                    # .wvd device so the challenge uses privacy mode, then feed
                    # the resulting content keys to ISA as ClearKey.
                    keys = None
                    unencrypted = False
                    if widevine.is_enabled():
                        try:
                            keys = widevine.get_content_keys(
                                session, api, post, asset_id, program_id, file_id,
                                source['url'], license_cert,
                                drm_declared = source['license'] is not None)
                            # None means the manifest carries no DRM at all
                            unencrypted = keys is None
                        except Exception as e:
                            import traceback
                            widevine.log('local CDM failed: %s\n%s'
                                         % (e, traceback.format_exc()), xbmc.LOGERROR)
                            # Short, readable reason on screen; the raw API
                            # error stays in the log.
                            xbmcgui.Dialog().notification(
                                'Vodafone TV', widevine.friendly_error(e),
                                xbmcgui.NOTIFICATION_WARNING, 6000)

                            # Remember channels outside the subscription so the
                            # list can leave them out next time.
                            if getattr(e, 'code', None) == entitlement.NOT_ENTITLED_CODE:
                                entitlement.mark_unentitled(asset_id)

                            # Do not hand over to ISA: it cannot do privacy mode
                            # (#1850) so it cannot succeed, and its 7-8 license
                            # retries each book a slot and start a session,
                            # which is what keeps the account's single session
                            # slot occupied. Fail cleanly instead.
                            if not widevine.isa_fallback_enabled():
                                xbmcplugin.setResolvedUrl(_handle, False, list_item)
                                return

                    if unencrypted:
                        # Clear channel (e.g. DVTV Extra): no DRM properties,
                        # and no ccursession session to release afterwards.
                        widevine.log('unencrypted channel -- playing without DRM')
                    elif keys:
                        set_clearkey_properties(list_item, keys)
                        # let the service release the session when playback ends
                        widevine.remember_playback(asset_id, program_id, file_id)
                        entitlement.clear_unentitled(asset_id)
                    else:
                        set_widevine_properties(list_item, license_url, license_cert)

                    if from_start:
                        # On a live (dynamic) manifest ISA starts at the live
                        # edge; this makes it start at the beginning of the
                        # timeshift window instead, i.e. the start of the show.
                        # Harmless on a static manifest, which starts there
                        # anyway.
                        list_item.setProperty('inputstream.adaptive.play_timeshift_buffer', 'true')
                    else:
                        # Live-edge playback: keep a little behind the edge so
                        # ISA never requests a segment Vodafone has not finished
                        # generating -- that cold-segment wait is what freezes
                        # live playback for ~10 s every so often.
                        delay = live_delay()
                        if delay > 0:
                            list_item.setProperty('inputstream.adaptive.live_delay', str(delay))

                    list_item.setProperty("inputstream.adaptive.stream_headers", "verifypeer=false")
                    list_item.setMimeType('application/dash+xml')
                    list_item.setContentLookup(False)       
                    xbmcplugin.setResolvedUrl(_handle, True, list_item)

                else:
                    playback_error()
            else:
                playback_error()
    else:
        xbmcgui.Dialog().notification('Vodafone TV','Nesprávný PIN', xbmcgui.NOTIFICATION_ERROR, 5000)


def get_keepalive_url(mpd, response):
    keepalive = None
    dom = minidom.parseString(response.read())
    adaptationSets = dom.getElementsByTagName('AdaptationSet')
    for adaptationSet in adaptationSets:
        if adaptationSet.getAttribute('contentType') == 'video':
            maxBandwidth = adaptationSet.getAttribute('maxBandwidth')
            segmentTemplates = adaptationSet.getElementsByTagName('SegmentTemplate')
            for segmentTemplate in segmentTemplates:
                timelines = segmentTemplate.getElementsByTagName('S')
                for timeline in timelines:
                    ts = timeline.getAttribute('t')
                uri = 'dash/' + segmentTemplate.getAttribute('media').replace('&amp;', '&').replace('$RepresentationID$', 'video=' + maxBandwidth).replace('$Time$', ts)
                keepalive = mpd.replace('manifest.mpd?bkm-query', uri)
    return keepalive
# -*- coding: utf-8 -*-
"""Local channel-logo cache.

Kodi loads the icons of a whole channel list in one burst. The Kaltura image
server throttles that and answers most requests with a JSON error instead of an
image, so only a few logos show -- in the addon's own live list and in a PVR
client alike. Downloading each logo once, sequentially, into the addon data
folder and then pointing Kodi at the local files sidesteps the throttling.

Shared by the direct IPTV Simple export (libs/export.py) and the live listing
(libs/live.py); the background service (service.py) keeps the cache filled.
"""
import os
import time

import xbmc
import xbmcaddon
import xbmcvfs

from urllib.request import urlopen, Request

from libs.utils import plugin_id

LOGO_DIR = 'logos'

# Once every logo is cached, only look again this often (new channels are rare).
# While some are still missing, retry on the shorter interval instead, since the
# gaps are usually just rate-limited downloads that succeed on a later pass.
REFRESH_INTERVAL = 6 * 60 * 60
RETRY_INTERVAL = 15 * 60
# Space successive downloads out: the image server rate-limits a fast run and
# starts refusing, which is what leaves a chunk of logos missing after one pass.
DOWNLOAD_DELAY = 200  # ms
_last_refresh = 0
_all_cached = False

# A browser User-Agent for the download, and a cap so a dead image never stalls
# the caching pass.
LOGO_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
           '(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36')
LOGO_TIMEOUT = 15


def _log(message, level=xbmc.LOGINFO):
    xbmc.log('Vodafone TV logos > %s' % message, level)


def cache_dir():
    """The folder the logos live in (created if missing)."""
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    path = os.path.join(profile, LOGO_DIR)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _file(channel_id):
    return os.path.join(cache_dir(), '%s.png' % channel_id)


def _special(channel_id):
    """A special:// path to the cached file -- portable and, because it carries
    ``://``, taken as-is by IPTV Simple and resolved by Kodi."""
    return 'special://profile/addon_data/%s/%s/%s.png' % (
        plugin_id, LOGO_DIR, channel_id)


def logo_for(channel):
    """The cached local logo for a channel, or its remote URL as a fallback.

    A cheap existence check, so it is fine to call while building a list.
    """
    channel_id = channel['id']
    path = os.path.join(cache_dir(), '%s.png' % channel_id)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return _special(channel_id)
    return channel.get('logo') or ''


def _download(url, dest):
    """Fetch one logo to ``dest``; True on success.

    A non-image reply (the throttle JSON) is retried with a short back-off.
    """
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': LOGO_UA}),
                         timeout=LOGO_TIMEOUT) as response:
                content_type = response.headers.get('Content-Type', '')
                body = response.read()
            if body and content_type.lower().startswith('image/'):
                with open(dest, 'wb') as handle:
                    handle.write(body)
                return True
            xbmc.sleep(500 * (attempt + 1))
        except Exception as error:
            _log('download failed (%s): %s' % (url, error), xbmc.LOGWARNING)
            xbmc.sleep(500 * (attempt + 1))
    return False


def cache(channels, on_progress=None):
    """Download any not-yet-cached channel logos, one at a time.

    Returns the number now available locally. Honors an abort request so it
    never holds up a shutdown. Already-cached logos are skipped, so only the
    first pass pays the cost.
    """
    monitor = xbmc.Monitor()
    directory = cache_dir()
    available = 0
    total = len(channels)
    for done, channel in enumerate(channels, 1):
        if monitor.abortRequested():
            break
        url = channel.get('logo')
        if url:
            dest = os.path.join(directory, '%s.png' % channel['id'])
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                available += 1
            else:
                xbmc.sleep(DOWNLOAD_DELAY)  # pace requests to dodge rate-limiting
                if _download(url, dest):
                    available += 1
        if on_progress:
            on_progress(done, total)
    return available


def missing(channels):
    """How many channels still have no cached logo (for the service to decide
    whether a caching pass is worth running)."""
    directory = cache_dir()
    count = 0
    for channel in channels:
        if not channel.get('logo'):
            continue
        dest = os.path.join(directory, '%s.png' % channel['id'])
        if not (os.path.exists(dest) and os.path.getsize(dest) > 0):
            count += 1
    return count


def enabled():
    """Whether logos should be cached locally (default: yes)."""
    return xbmcaddon.Addon().getSetting('cache_logos') != 'false'


def refresh_if_needed():
    """Fill the logo cache in the background, so the addon's own channel list
    (not just a PVR client) shows icons. Cheap to call from the service loop:
    retries on the short interval while logos are still missing, then backs off
    once everything is cached."""
    global _last_refresh, _all_cached
    if not enabled():
        return
    now = time.time()
    interval = REFRESH_INTERVAL if _all_cached else RETRY_INTERVAL
    if _last_refresh and now - _last_refresh < interval:
        return
    _last_refresh = now

    from libs import guide
    try:
        channels = guide.build_channels()
    except Exception as error:
        _log('could not read channels for logo cache: %s' % error, xbmc.LOGWARNING)
        return
    if not channels:
        return
    if missing(channels):
        cache(channels)
    remaining = missing(channels)
    _all_cached = remaining == 0
    _log('logo cache: %d of %d channels missing' % (remaining, len(channels)))

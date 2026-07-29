# -*- coding: utf-8 -*-
"""Direct IPTV Simple export.

Writes a plain M3U playlist and an XMLTV programme guide to the addon's data
folder, so IPTV Simple can be pointed straight at the files (local path) without
needing IPTV Manager. The line-up and guide come from libs.guide, so this stays
in step with the IPTV Manager integration (same channels, same entitlement
filtering).

The files are regenerated in the background by service.py on an interval and can
also be produced on demand from the settings screen.
"""
import os
import time
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from libs import guide
from libs import logos
from libs.utils import plugin_id

PLAYLIST_NAME = 'playlist.m3u8'
EPG_NAME = 'epg.xml'
GROUP_TITLE = 'Vodafone TV'
CATCHUP_DAYS = 7

# IPTV Simple substitutes the selected programme's start/end (unix UTC) into
# {utc}/{utcend} and plays this URL; play_catchup then replays that window.
CATCHUP_SOURCE = ('plugin://%s/?action=play_catchup&channel_id=%%s'
                  '&start={utc}&end={utcend}') % plugin_id


def _log(message, level=xbmc.LOGINFO):
    xbmc.log('Vodafone TV export > %s' % message, level)


def _addon():
    return xbmcaddon.Addon()


def enabled():
    """Whether the background export is switched on."""
    return _addon().getSetting('iptvsimple_export') == 'true'


def _interval_seconds():
    try:
        hours = int(_addon().getSetting('iptvsimple_interval'))
    except (ValueError, TypeError):
        hours = 12
    return max(1, hours) * 3600


def output_dir():
    """The addon data folder the files are written to (created if missing)."""
    path = xbmcvfs.translatePath(_addon().getAddonInfo('profile'))
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def playlist_path():
    return os.path.join(output_dir(), PLAYLIST_NAME)


def epg_path():
    return os.path.join(output_dir(), EPG_NAME)


# --- serialisation --------------------------------------------------------

def _xmltv_time(ts):
    """XMLTV timestamp in UTC, e.g. ``20260723210000 +0000``."""
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime('%Y%m%d%H%M%S +0000')


def _m3u(channels):
    lines = ['#EXTM3U']
    for channel in channels:
        lines.append(
            '#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-chno="%s" tvg-logo="%s" '
            'group-title="%s" catchup="default" catchup-days="%d" '
            'catchup-source="%s",%s' % (
                channel['id'], channel['name'], channel['number'],
                logos.logo_for(channel), GROUP_TITLE, CATCHUP_DAYS,
                CATCHUP_SOURCE % channel['id'], channel['name']))
        lines.append(channel['stream'])
    return '\n'.join(lines) + '\n'


def _programme_xml(item, channel_id):
    out = ['  <programme start="%s" stop="%s" channel="%s">' % (
        _xmltv_time(item['startts']), _xmltv_time(item['endts']), escape(channel_id))]
    out.append('    <title>%s</title>' % escape(item.get('title') or ''))
    if item.get('episodeName'):
        out.append('    <sub-title>%s</sub-title>' % escape(item['episodeName']))
    if item.get('description'):
        out.append('    <desc>%s</desc>' % escape(item['description']))
    for genre in item.get('genres') or []:
        out.append('    <category>%s</category>' % escape(genre))
    image = item.get('cover') or item.get('poster')
    if image:
        out.append('    <icon src=%s />' % quoteattr(image))
    episode = item.get('episodeNumber')
    season = item.get('seasonNumber')
    if episode and int(episode) > 0:
        if season and int(season) > 0:
            num = 'S%dE%d' % (int(season), int(episode))
        else:
            num = 'E%d' % int(episode)
        out.append('    <episode-num system="onscreen">%s</episode-num>' % escape(num))
    if item.get('year'):
        out.append('    <date>%s</date>' % escape(str(item['year'])))
    out.append('  </programme>')
    return '\n'.join(out)


def _xmltv(channels, epg):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
           '<tv generator-info-name="plugin.video.vodafonetv">']
    for channel in channels:
        out.append('  <channel id="%s">' % escape(channel['id']))
        out.append('    <display-name>%s</display-name>' % escape(channel['name']))
        logo = logos.logo_for(channel)
        if logo:
            out.append('    <icon src=%s />' % quoteattr(logo))
        out.append('  </channel>')
    for channel in channels:
        for item in epg.get(channel['id'], []):
            out.append(_programme_xml(item, channel['id']))
    out.append('</tv>')
    return '\n'.join(out) + '\n'


def _write(path, text):
    handle = xbmcvfs.File(path, 'w')
    try:
        handle.write(bytearray(text.encode('utf-8')))
    finally:
        handle.close()


# --- generation -----------------------------------------------------------

def write_files(on_progress=None):
    """Build the line-up and guide and write both files.

    Returns ``(channel_count, programme_count)``. Writes nothing and returns
    ``(0, 0)`` when there are no channels to export.
    """
    channels = guide.build_channels()
    if not channels:
        _log('no channels to export', xbmc.LOGWARNING)
        return 0, 0

    def phase(label):
        if on_progress is None:
            return None
        return lambda done, total: on_progress(done, total, label)

    cached = logos.cache(channels, on_progress=phase('Loga'))
    _write(playlist_path(), _m3u(channels))
    epg = guide.build_epg(channels, on_progress=phase('EPG'))
    _write(epg_path(), _xmltv(channels, epg))

    programmes = sum(len(items) for items in epg.values())
    _log('wrote %d channels (%d logos cached) and %d programmes to %s'
         % (len(channels), cached, programmes, output_dir()))
    return len(channels), programmes


def refresh_if_due():
    """Regenerate in the background when enabled and the playlist is stale.

    Staleness is judged from the playlist file's age, so no extra state is
    kept. Cheap to call often -- it only stat()s a file until a rebuild is
    actually due.
    """
    if not enabled():
        return
    try:
        age = time.time() - os.path.getmtime(playlist_path())
    except OSError:
        age = None  # never generated yet
    if age is not None and age < _interval_seconds():
        return
    try:
        write_files()
    except Exception as error:
        _log('background export failed: %s' % error, xbmc.LOGWARNING)


def _generate_with_progress():
    """Build both files behind a background progress bar.

    Returns ``(channel_count, programme_count)``, or ``(None, None)`` if the
    build failed (a notification is shown in that case).
    """
    dialog = xbmcgui.DialogProgressBG()
    dialog.create('Vodafone TV', 'Generuji playlist a EPG…')
    try:
        return write_files(on_progress=lambda done, total, label: dialog.update(
            int(done * 100 / total), message='%s %d / %d' % (label, done, total)))
    except Exception as error:
        _log('export failed: %s' % error, xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Generování selhalo: %s' % error,
                                      xbmcgui.NOTIFICATION_ERROR, 6000)
        return None, None
    finally:
        dialog.close()


def generate_now():
    """Produce the files on demand, then offer to auto-configure IPTV Simple."""
    channels, programmes = _generate_with_progress()
    if channels is None:
        return
    if not channels:
        xbmcgui.Dialog().notification('Vodafone TV', 'Žádné kanály k exportu',
                                      xbmcgui.NOTIFICATION_WARNING, 5000)
        return

    if xbmcgui.Dialog().yesno(
            'Vodafone TV',
            'Uloženo %d kanálů a %d pořadů.\n\n'
            'Nastavit vyhrazený IPTV Simple klient „%s" automaticky?'
            % (channels, programmes, INSTANCE_NAME),
            yeslabel='Ano', nolabel='Ne, cesty ručně'):
        configure_iptvsimple(regenerate=False)
    else:
        xbmcgui.Dialog().ok(
            'Vodafone TV',
            'Nastav v IPTV Simple Client (typ cesty: Místní):\n'
            'M3U: %s\nEPG: %s' % (playlist_path(), epg_path()))


# --- automatic IPTV Simple client set-up ----------------------------------

IPTVSIMPLE_ID = 'pvr.iptvsimple'
INSTANCE_NAME = 'Vodafone TV CZ'


def _iptvsimple_data_dir():
    return xbmcvfs.translatePath('special://profile/addon_data/%s/' % IPTVSIMPLE_ID)


def _set_addon_enabled(addon_id, enabled):
    import json
    xbmc.executeJSONRPC(json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.SetAddonEnabled',
        'params': {'addonid': addon_id, 'enabled': enabled}}))


def _addon_enabled(addon_id):
    """Current enabled state (True/False), or None if it can't be read."""
    import json
    try:
        resp = json.loads(xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.GetAddonDetails',
            'params': {'addonid': addon_id, 'properties': ['enabled']}})))
        return resp['result']['addon']['enabled']
    except Exception:
        return None


def _reload_iptvsimple():
    """Reload IPTV Simple without restarting Kodi.

    Disabling then re-enabling the addon makes Kodi unload it and, on the way
    back up, re-read its files and re-enumerate the instance-settings-*.xml
    files -- so a freshly written instance appears live. This is what IPTV
    Manager does too. Skipped (returns False) while TV/radio is playing, so we
    never kill an active stream.

    Toggling too fast leaves the PVR client half-initialised -- the channels are
    missing until a (slower) manual disable/enable. So wait for the disable to
    actually register and give the PVR manager time to tear the client down
    before re-enabling.
    """
    if (xbmc.getCondVisibility('Pvr.IsPlayingTv')
            or xbmc.getCondVisibility('Pvr.IsPlayingRadio')):
        _log('IPTV Simple is in use; skipping reload', xbmc.LOGINFO)
        return False

    monitor = xbmc.Monitor()
    _set_addon_enabled(IPTVSIMPLE_ID, False)
    # Wait until Kodi reports it disabled (up to ~6 s), then a settle margin.
    for _ in range(24):
        if monitor.waitForAbort(0.25):
            return True
        if _addon_enabled(IPTVSIMPLE_ID) is False:
            break
    monitor.waitForAbort(3)
    _set_addon_enabled(IPTVSIMPLE_ID, True)
    return True


def _instance_file(name):
    """Where to write the instance for ``name``.

    Kodi enumerates IPTV Simple instances by globbing ``instance-settings-*.xml``
    in the addon's data folder, so an existing one with our name is reused (kept
    idempotent) and otherwise the next free numeric id is taken. Returns
    ``(path, is_new)``.
    """
    import glob
    import re

    data_dir = _iptvsimple_data_dir()
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)

    max_id = 0
    marker = '<setting id="kodi_addon_instance_name">%s</setting>' % name
    for path in glob.glob(os.path.join(data_dir, 'instance-settings-*.xml')):
        match = re.search(r'instance-settings-(\d+)\.xml$', path)
        if not match:
            continue
        max_id = max(max_id, int(match.group(1)))
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                if marker in handle.read():
                    return path, False
        except OSError:
            continue
    return os.path.join(data_dir, 'instance-settings-%d.xml' % (max_id + 1)), True


def _instance_xml(name):
    return (
        '<settings version="2">\n'
        '    <setting id="kodi_addon_instance_name">%s</setting>\n'
        '    <setting id="kodi_addon_instance_enabled">true</setting>\n'
        '    <setting id="m3uPathType">0</setting>\n'
        '    <setting id="m3uPath">%s</setting>\n'
        '    <setting id="m3uRefreshMode">1</setting>\n'
        '    <setting id="m3uRefreshIntervalMins">60</setting>\n'
        '    <setting id="epgPathType">0</setting>\n'
        '    <setting id="epgPath">%s</setting>\n'
        '    <setting id="catchupEnabled">true</setting>\n'
        '    <setting id="catchupDays">%d</setting>\n'
        # Don't offer catch-up for the currently-airing programme, so seeking a
        # live channel stays an in-player DASH seek instead of IPTV Simple
        # re-resolving the catch-up source on every seek (which tears the stream
        # down and re-buffers -- the "clunky" live-seek). Finished programmes
        # still catch up normally from the guide.
        '    <setting id="catchupOnlyOnFinishedProgrammes">true</setting>\n'
        '</settings>\n'
        % (escape(name), escape(playlist_path()), escape(epg_path()), CATCHUP_DAYS))


def configure_iptvsimple(regenerate=True):
    """Create (or update) a dedicated IPTV Simple client for Vodafone TV.

    Writes the instance settings file with the M3U/EPG paths already filled in,
    then reloads IPTV Simple so the client comes up without a Kodi restart. If
    it can't reload right now (TV is playing), the user is told to restart.
    """
    try:
        xbmcaddon.Addon(IPTVSIMPLE_ID)
    except Exception:
        xbmcgui.Dialog().ok(
            'Vodafone TV',
            'IPTV Simple Client (pvr.iptvsimple) není nainstalovaný.\n'
            'Nainstaluj ho a zkus to znovu.')
        return

    # Make sure there is something to point the client at.
    if regenerate and not os.path.exists(playlist_path()):
        channels, _ = _generate_with_progress()
        if not channels:
            if channels == 0:
                xbmcgui.Dialog().notification('Vodafone TV', 'Žádné kanály k exportu',
                                              xbmcgui.NOTIFICATION_WARNING, 5000)
            return

    path, is_new = _instance_file(INSTANCE_NAME)
    try:
        _write(path, _instance_xml(INSTANCE_NAME))
    except Exception as error:
        _log('could not write instance file %s: %s' % (path, error), xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Vodafone TV', 'Nastavení klienta selhalo: %s' % error,
                                      xbmcgui.NOTIFICATION_ERROR, 6000)
        return
    _log('%s IPTV Simple instance "%s" at %s'
         % ('created' if is_new else 'updated', INSTANCE_NAME, path))
    reloaded = _reload_iptvsimple()

    verb = 'vytvořen' if is_new else 'aktualizován'
    if reloaded:
        message = ('TV klient „%s" byl %s a načten.\n\n'
                   'Kanály najdeš za chvíli v části Živá TV.' % (INSTANCE_NAME, verb))
    else:
        message = ('TV klient „%s" byl %s.\n\n'
                   'Právě běží přehrávání, takže se načte po jeho ukončení '
                   'nebo po restartu Kodi.' % (INSTANCE_NAME, verb))
    if _addon().getSetting('iptv.enabled') == 'true':
        message += ('\n\nPozn.: máš zapnutou i integraci IPTV Manager, takže '
                    'kanály mohou být dvakrát. Ponech jen jednu možnost.')
    xbmcgui.Dialog().ok('Vodafone TV', message)

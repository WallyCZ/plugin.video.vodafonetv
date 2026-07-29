# -*- coding: utf-8 -*-
import sys
import xbmcaddon
import xbmc

from libs.proxy import ProxyServer

addon = xbmcaddon.Addon()


class PlaybackMonitor(xbmc.Player):
    """Release the playback session when our stream stops.

    The account allows one concurrent session and a start holds it for ~300 s,
    so without a teardown the next playback is refused with NagraSSMException
    1007. Only acts when libs.stream recorded a playback, so other players in
    Kodi are left alone.
    """

    def _release(self):
        from libs import widevine
        playing = widevine.take_playback()
        if not playing:
            return
        try:
            from libs.api import API
            from libs.session import Session
            widevine.teardown(Session(), API(), *playing)
        except Exception as e:
            widevine.log('teardown after playback failed: %s' % e, xbmc.LOGWARNING)

    def onPlayBackStopped(self):
        self._release()

    def onPlayBackEnded(self):
        self._release()

    def onPlayBackError(self):
        self._release()


if __name__ == '__main__':
    monitor = xbmc.Monitor()
    player = PlaybackMonitor()
    # Initialize proxy server with random port
    proxy = ProxyServer(host='127.0.0.1', port=0)
    # Store the assigned port in settings
    addon.setSetting('proxy_port', str(proxy.get_port()))
    proxy.start()
    
    xbmc.log('VodafoneTV proxy service started on port {}'.format(proxy.get_port()), xbmc.LOGINFO)

    while not monitor.abortRequested():
        # Regenerate the IPTV Simple export when it is due. Skipped while
        # something is playing so it does not compete with playback for the
        # single session; refresh_if_due() is otherwise a cheap file stat.
        if not player.isPlaying():
            from libs import export
            export.refresh_if_due()
            # Keep the local logo cache filled so channel lists (the addon's own
            # and any PVR client) show icons instead of being throttled.
            from libs import logos
            logos.refresh_if_needed()
        if monitor.waitForAbort(10):
            break

    proxy.stop()
    xbmc.log('VodafoneTV proxy service stopped', xbmc.LOGINFO)
    sys.exit()
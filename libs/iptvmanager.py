# -*- coding: utf-8 -*-
"""IPTV Manager integration.

https://github.com/add-ons/service.iptv.manager/wiki/Integration

IPTV Manager binds a localhost socket, launches one of our plugin routes with
the port appended (``?action=iptv_channels&port=NNNN``), and reads a single
JSON document back over that socket. We answer with the channel line-up
(JSON-STREAMS) and the programme guide (JSON-EPG).

The channels and EPG come from libs.guide, which is shared with the direct
IPTV Simple export. Those builders run unattended and never pop up a dialog.
"""
import json
import socket

import xbmc

from libs import guide
from libs.utils import plugin_id


def _log(message, level=xbmc.LOGINFO):
    xbmc.log('Vodafone TV IPTV Manager > %s' % message, level)


class IPTVManager:
    """Sends channel and EPG data to IPTV Manager over its callback socket."""

    def __init__(self, port):
        self.port = int(port)

    def via_socket(func):
        """Send whatever the wrapped method returns as JSON, then hang up.

        Connect back to IPTV Manager *first*, then build the payload. IPTV
        Manager gives the addon only ~10 s to connect (``socket.accept``), but
        once connected it waits without a timeout for the data. Building the
        guide makes one API call per channel and easily takes longer than that,
        so the connection has to be established before any of that work starts,
        not after -- otherwise the accept times out and the refresh fails.
        """
        def send(self):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('127.0.0.1', self.port))
            try:
                data = func(self)
                sock.sendall(json.dumps(data).encode('utf-8'))
            except Exception as error:
                import traceback
                _log('failed to build data: %s\n%s' % (error, traceback.format_exc()),
                     xbmc.LOGERROR)
            finally:
                sock.close()
        return send

    @via_socket
    def send_channels(self):
        """JSON-STREAMS: the visible channel line-up."""
        streams = [dict(id=c['id'], name=c['name'], preset=c['number'],
                        logo=c['logo'], stream=c['stream'])
                   for c in guide.build_channels()]
        return dict(version=1, streams=streams)

    @via_socket
    def send_epg(self):
        """JSON-EPG: the programme guide keyed by channel id."""
        import time
        now = int(time.time())
        epg = {}
        for channel_id, items in guide.build_epg(guide.build_channels()).items():
            epg[channel_id] = [self._programme(item, now) for item in items]
        return dict(version=1, epg=epg)

    def _programme(self, item, now):
        programme = dict(
            start=guide.iso(item['startts']),
            stop=guide.iso(item['endts']),
            title=item.get('title') or '',
        )
        if item.get('description'):
            programme['description'] = item['description']
        if item.get('episodeName'):
            programme['subtitle'] = item['episodeName']
        image = item.get('cover') or item.get('poster')
        if image:
            programme['image'] = image
        if item.get('genres'):
            programme['genre'] = ', '.join(item['genres'])
        if item.get('year'):
            programme['date'] = str(item['year'])
        episode = item.get('episodeNumber')
        season = item.get('seasonNumber')
        if episode and int(episode) > 0:
            if season and int(season) > 0:
                programme['episode'] = 'S%dE%d' % (int(season), int(episode))
            else:
                programme['episode'] = 'E%d' % int(episode)

        # Finished programmes within the archive window can be replayed straight
        # from the guide; hand IPTV Manager a catch-up stream for them.
        if int(item['endts']) < now and item.get('id') and item.get('channel_id'):
            programme['stream'] = (
                'plugin://%s/?action=play_archive&id=%s&channel_id=%s'
                '&startts=%s&endts=%s' % (
                    plugin_id, item['id'], item['channel_id'],
                    int(item['startts']) - 1, int(item['endts'])))
        return programme

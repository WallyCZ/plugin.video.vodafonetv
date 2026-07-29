# -*- coding: utf-8 -*-
"""Shared channel and EPG builders.

One source of truth for the line-up and programme guide, used both by the IPTV
Manager callbacks (libs/iptvmanager.py) and by the direct IPTV Simple export
(libs/export.py). The channel list is served from the on-disk cache and needs
no session; the EPG reaches the live API and needs a signed-in one.
"""
from contextlib import contextmanager
from datetime import datetime, timezone

import xbmc
import xbmcaddon

from libs.utils import plugin_id

# EPG window: seven days back to match Vodafone's catch-up archive (so every
# programme a PVR client can replay is present in the guide) and three days
# ahead for the upcoming schedule. list_api pages through the whole range, so a
# wide window never truncates -- it just fetches more.
EPG_PAST_DAYS = 7
EPG_FUTURE_DAYS = 3


def log(message, level=xbmc.LOGINFO):
    xbmc.log('Vodafone TV guide > %s' % message, level)


@contextmanager
def silent():
    """Run a block with session logins forced never to prompt.

    Channel and EPG building runs unattended (a background service task or an
    IPTV Manager callback), so a login must not put up a dialog. With
    ``session.NO_PROMPT`` set, an already-registered device signs in silently
    (via /udid), while a device that would need registering gives up instead of
    asking.
    """
    from libs import session as session_mod
    previous = session_mod.NO_PROMPT
    session_mod.NO_PROMPT = True
    try:
        yield
    finally:
        session_mod.NO_PROMPT = previous


def iso(ts):
    """Kaltura epoch seconds -> ISO 8601 (UTC), the form IPTV Manager expects."""
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def live_stream_url(channel_id):
    """The plugin path that plays a channel live."""
    return 'plugin://%s/?action=play_live&id=%s' % (plugin_id, channel_id)


def _number_mode():
    """Which channel number to export (tvg-chno): see settings 30095.

    ``vodafone`` -- the official Vodafone number (vdf_number); ``addon`` -- the
    addon's own, possibly manually renumbered channel_number; ``sequential`` --
    1..N in list order. Defaults to the Vodafone numbering.
    """
    return xbmcaddon.Addon().getSetting('export_channel_numbers') or 'vodafone'


def _vodafone_number(channel):
    return channel.get('vdf_number') or channel['channel_number']


def build_channels():
    """The visible, subscribed line-up as a list of channel dicts.

    Each entry: ``id`` (str), ``name``, ``number`` (channel number, int),
    ``logo`` (may be ''), ``stream`` (plugin play path). Ordered by the exported
    channel number. Served from the channel cache, so no session is needed
    unless the cache has expired; entitlement filtering matches the live
    listing.
    """
    from libs.channels import Channels
    from libs import entitlement

    with silent():
        try:
            channels = Channels().get_channels_list('id')
        except SystemExit:
            log('channel cache stale and cannot sign in silently; no channels',
                xbmc.LOGWARNING)
            return []

        # Same rule as the live listing: when "hide unsubscribed" is on, only
        # offer channels the household is actually entitled to.
        statuses = {}
        hide_unsubscribed = entitlement.hide_enabled()
        if hide_unsubscribed:
            try:
                statuses = entitlement.refresh_statuses(
                    [c['fileId'] for c in channels.values() if c.get('fileId')])
            except SystemExit:
                log('not signed in; cannot check entitlement, offering all '
                    'channels', xbmc.LOGWARNING)
                hide_unsubscribed = False

    # Order by the number we are about to export, so the list reads in order.
    mode = _number_mode()
    sort_key = (_vodafone_number if mode == 'vodafone'
                else (lambda c: c['channel_number']))

    result = []
    hidden = 0
    position = 0
    for cid in sorted(channels, key=lambda c: sort_key(channels[c])):
        channel = channels[cid]
        if hide_unsubscribed and entitlement.is_unentitled(
                channel['id'], channel.get('fileId'), statuses):
            hidden += 1
            continue
        position += 1
        if mode == 'sequential':
            number = position
        elif mode == 'addon':
            number = channel['channel_number']
        else:
            number = _vodafone_number(channel)
        result.append(dict(
            id=str(channel['id']),
            name=channel['name'],
            number=number,
            logo=channel.get('logo') or '',
            stream=live_stream_url(channel['id']),
        ))
    if hidden:
        log('%d channel(s) omitted as not subscribed' % hidden)
    return result


def build_epg(channels, on_progress=None):
    """Raw programme items keyed by channel id, for the given channel list.

    Returns ``{channel_id (str): [item, ...]}`` where each item is the raw dict
    from ``libs.epg.get_channel_epg`` (startts/endts/title/...), sorted by start
    time. Only the passed-in channels are fetched, so the guide covers exactly
    the exported line-up. Returns ``{}`` if no session can be established.

    ``on_progress(done, total)`` is called after each channel, if given, so a
    caller can drive a progress dialog.
    """
    import time
    from libs.epg import get_channel_epg
    from libs.session import Session

    now = int(time.time())
    from_ts = now - EPG_PAST_DAYS * 24 * 60 * 60
    to_ts = now + EPG_FUTURE_DAYS * 24 * 60 * 60

    epg = {}
    with silent():
        # The EPG hits the live API, which needs a signed-in session. Create it
        # once up front (silently); if that is not possible, skip the EPG rather
        # than letting every channel try and fail.
        try:
            Session()
        except SystemExit:
            log('not signed in and cannot register silently; EPG skipped',
                xbmc.LOGWARNING)
            return {}
        except Exception as error:
            log('could not establish a session; EPG skipped: %s' % error,
                xbmc.LOGWARNING)
            return {}

        monitor = xbmc.Monitor()
        total = len(channels)
        for done, channel in enumerate(channels, 1):
            if monitor.abortRequested():
                break
            try:
                programmes = get_channel_epg(channel['id'], from_ts, to_ts)
            except Exception as error:
                log('EPG for %s failed: %s' % (channel.get('name'), error),
                    xbmc.LOGWARNING)
                continue
            epg[str(channel['id'])] = [programmes[key]
                                       for key in sorted(programmes.keys())]
            if on_progress:
                on_progress(done, total)
    return epg

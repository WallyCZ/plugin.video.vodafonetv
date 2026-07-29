# -*- coding: utf-8 -*-
import sys
import xbmcgui
import xbmcplugin
import xbmcaddon

from datetime import datetime

from libs.channels import Channels
from libs.epg import get_live_epg, epg_listitem
from libs import logos
from libs.utils import get_url
from libs.stream import addon_live_mode, CATCHUP_MODE, LIVE_MODE
from libs import entitlement

if len(sys.argv) > 1:
    _handle = int(sys.argv[1])

def list_live(label):
    addon = xbmcaddon.Addon()
    xbmcplugin.setPluginCategory(_handle, label)
    channels = Channels()
    channels_list = channels.get_channels_list('channel_number')
    epg = get_live_epg()
    hide_unsubscribed = entitlement.hide_enabled()
    statuses = {}
    if hide_unsubscribed:
        # productprice/list knows which media files the household may watch;
        # cached, so this costs a few calls at most twice a day.
        statuses = entitlement.refresh_statuses(
            [c['fileId'] for c in channels_list.values() if c.get('fileId')])
    hidden = 0
    cnt = 0
    for num in sorted(channels_list.keys()):
        if hide_unsubscribed and entitlement.is_unentitled(
                channels_list[num]['id'], channels_list[num].get('fileId'), statuses):
            hidden += 1
            continue
        cnt += 1
        if addon.getSetting('channel_numbers') == 'číslo kanálu':
            channel_number = str(num) + '. '
        elif addon.getSetting('channel_numbers') == 'pořadové číslo':
            channel_number = str(cnt) + '. '
        else:
            channel_number = ''
        # Prefer the locally cached logo -- Kodi throttles a burst of remote
        # icons, so a channel list of raw URLs mostly shows blanks.
        logo = logos.logo_for(channels_list[num])
        if channels_list[num]['id'] in epg:
            epg_item = epg[channels_list[num]['id']]
            list_item = xbmcgui.ListItem(label = channel_number + channels_list[num]['name'] + ' | ' + epg_item['title'] + ' | ' + datetime.fromtimestamp(epg_item['startts']).strftime('%H:%M') + ' - ' + datetime.fromtimestamp(epg_item['endts']).strftime('%H:%M'))
            list_item = epg_listitem(list_item = list_item, epg = epg_item, logo = logo)
        else:
            epg_item = {}
            list_item = xbmcgui.ListItem(label = channel_number + channels_list[num]['name'])
            list_item.setInfo('video', {'mediatype':'movie', 'title': channels_list[num]['name']})
        # The cached channel logo is the dependable thumbnail. epg_listitem
        # would otherwise use the current programme's cover, which is a remote
        # image the server throttles just like the logos -- so most of the list
        # would stay blank. The programme's poster/fanart still rides along.
        list_item.setArt({'thumb': logo, 'icon': logo})
        list_item.setContentLookup(False)
        list_item.setProperty('IsPlayable', 'true')
        # The addon's own list plays with its own default (setting
        # `addon_live_mode`, default live), independent of the PVR feed's
        # `live_mode`, so passing it explicitly here.
        addon_mode = addon_live_mode()
        url = get_url(action = 'play_live', id = channels_list[num]['id'],
                      title = channels_list[num]['name'], mode = addon_mode)
        # The live manifest carries almost no timeshift buffer; playing the
        # programme from its start needs a CATCHUP manifest instead. Offer both
        # the start of the show and whichever mode is not the addon default.
        if channels_list[num]['id'] in epg:
            def channel_url(action, **extra):
                return get_url(action = action, id = channels_list[num]['id'],
                               title = channels_list[num]['name'], **extra)
            menu = [('Přehrát od začátku pořadu',
                     'PlayMedia(%s)' % channel_url('play_startover'))]
            if addon_mode == CATCHUP_MODE:
                menu.append(('Přehrát živě (bez přetáčení)',
                             'PlayMedia(%s)' % channel_url('play_live', mode = LIVE_MODE)))
            else:
                menu.append(('Přehrát s přetáčením',
                             'PlayMedia(%s)' % channel_url('play_live', mode = CATCHUP_MODE)))
            list_item.addContextMenuItems(menu)
        xbmcplugin.addDirectoryItem(_handle, url, list_item, False)
    if hidden:
        entitlement.log('%d channel(s) hidden as not subscribed' % hidden)
    xbmcplugin.endOfDirectory(_handle, cacheToDisc = False)



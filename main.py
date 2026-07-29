# -*- coding: utf-8 -*-
import os
import sys
import xbmcgui
import xbmcplugin
import xbmcaddon

from urllib.parse import parse_qsl

from libs.utils import get_url
from libs.live import list_live
from libs.archive import list_archive, list_archive_days, list_program
from libs.stream import play_live, play_startover, play_archive, play_recording, play_catchup, play_vod
from libs.vod import list_vod, list_vod_folders, list_vod_category, list_vod_series
from libs.channels import Channels, manage_channels, list_channels_list_backups, list_channels_edit, edit_channel, delete_channel, change_channels_numbers
from libs.channels import list_channels_groups, add_channel_group, edit_channel_group, edit_channel_group_list_channels, edit_channel_group_add_channel, edit_channel_group_add_all_channels, edit_channel_group_delete_channel, select_channel_group, delete_channel_group
from libs.recordings import list_recordings, delete_recording, delete_future_recording, list_future_recordings, list_series_recordings, delete_series_recording, list_planning_recordings, list_rec_days, future_program, add_recording
from libs.search import list_search, delete_search, program_search, list_series_episodes
from libs.settings import list_settings
from libs.session import Session
from libs.profiles import list_profiles, switch_profile

if len(sys.argv) > 1:
    _handle = int(sys.argv[1])

def main_menu():
    addon = xbmcaddon.Addon()
    icons_dir = os.path.join(addon.getAddonInfo('path'), 'resources','images')

    list_item = xbmcgui.ListItem(label='Živé vysílání')
    url = get_url(action='list_live', page = 1, label = 'Živé vysílání')  
    list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'livetv.png'), 'icon' : os.path.join(icons_dir , 'livetv.png') })
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    list_item = xbmcgui.ListItem(label='Archiv')
    url = get_url(action='list_archive', label = 'Archiv')
    list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'archive.png'), 'icon' : os.path.join(icons_dir , 'archive.png') })
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    list_item = xbmcgui.ListItem(label='Videotéka')
    url = get_url(action='list_vod', label = 'Videotéka')
    list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'categories.png'), 'icon' : os.path.join(icons_dir , 'categories.png') })
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    list_item = xbmcgui.ListItem(label='Nahrávky')
    url = get_url(action='list_recordings', label = 'Nahrávky')
    list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'recordings.png'), 'icon' : os.path.join(icons_dir , 'recordings.png') })
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    list_item = xbmcgui.ListItem(label='Profil')
    url = get_url(action='list_profiles', label = 'Profil')
    list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'settings.png'), 'icon' : os.path.join(icons_dir , 'settings.png') })
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    list_item = xbmcgui.ListItem(label='Vyhledávání')
    url = get_url(action='list_search', label = 'Vyhledávání')  
    list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'search.png'), 'icon' : os.path.join(icons_dir , 'search.png') })
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    if addon.getSetting('hide_settings') != 'true':
        list_item = xbmcgui.ListItem(label='Nastavení Vodafone TV')
        url = get_url(action='list_settings', label = 'Nastavení Vodafone TV')  
        list_item.setArt({ 'thumb' : os.path.join(icons_dir , 'settings.png'), 'icon' : os.path.join(icons_dir , 'settings.png') })
        xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    xbmcplugin.endOfDirectory(_handle)

def router(paramstring):
    # Nothing to check up front: the session layer asks for a sign-in only
    # when the device turns out not to be registered.
    params = dict(parse_qsl(paramstring))
    if params:
        if params['action'] == 'list_live':
            list_live(params['label'])

        elif params['action'] == 'list_archive':
            list_archive(params['label'])
        elif params['action'] == 'list_archive_days':
            list_archive_days(params['id'], params['label'])
        elif params['action'] == 'list_program':
            list_program(params['id'], params['day_min'], params['label'])

        elif params['action'] == 'list_vod':
            list_vod(params['label'])
        elif params['action'] == 'vod_folders':
            list_vod_folders(params['label'])
        elif params['action'] == 'vod_category':
            list_vod_category(params['id'], params['label'], params.get('page', 1))
        elif params['action'] == 'vod_series':
            list_vod_series(params['series_id'], params['label'], params.get('page', 1))

        elif params['action'] == 'list_recordings':
            list_recordings(params['label'])
        elif params['action'] == 'list_future_recordings':
            list_future_recordings(params['label'])
        elif params['action'] == 'list_series_recordings':
            list_series_recordings(params['label'])
        elif params['action'] == 'delete_recording':
            delete_recording(params['id'])
        elif params['action'] == 'delete_future_recording':
            delete_future_recording(params['id'])
        elif params['action'] == 'delete_series_recording':
            delete_series_recording(params['id'])
        elif params['action'] == 'list_planning_recordings':
            list_planning_recordings(params['label'])
        elif params['action'] == 'list_rec_days':
            list_rec_days(params['id'], params['label'])
        elif params['action'] == 'future_program':
            future_program(params['id'], params['day'], params['label'])
        elif params['action'] == 'add_recording':
            add_recording(params['id'])

        elif params['action'] == 'list_profiles':
            list_profiles(params['label'])
        elif params['action'] == 'switch_profile':
            switch_profile(params['id'])

        elif params['action'] == 'list_search':
            list_search(params['label'])
        elif params['action'] == 'program_search':
            program_search(params['query'], params['label'])
        elif params['action'] == 'list_series_episodes':
            list_series_episodes(params['series_id'], params.get('name', ''), params['label'])
        elif params['action'] == 'delete_search':
            delete_search(params['query'])

        elif params['action'] == 'play_live':
            play_live(params['id'], params.get('mode'))
        elif params['action'] == 'play_startover':
            play_startover(params['id'])
        elif params['action'] == 'play_archive':
            play_archive(params['id'], params.get('epg'), params.get('channel_id'),
                         params.get('startts'), params.get('endts'))
        elif params['action'] == 'play_vod':
            play_vod(params['asset_id'], params['file_id'])
        elif params['action'] == 'play_recording':
            play_recording(params['channel_id'], params['startts'], params['endts'])
        elif params['action'] == 'play_catchup':
            # Catch-up from a PVR client (IPTV Simple catchup-source): play the
            # past programme in this channel's window.
            play_catchup(params['channel_id'], params['start'], params['end'])

        elif params['action'] == 'iptv_channels':
            # IPTV Manager callback: connects back to the port it passed and
            # reads the channel line-up as JSON.
            from libs.iptvmanager import IPTVManager
            IPTVManager(params['port']).send_channels()
        elif params['action'] == 'iptv_epg':
            from libs.iptvmanager import IPTVManager
            IPTVManager(params['port']).send_epg()
        elif params['action'] == 'export_iptvsimple':
            # Manual "generate now" for the direct IPTV Simple export.
            from libs import export
            export.generate_now()
        elif params['action'] == 'configure_iptvsimple':
            # Create/update a dedicated IPTV Simple client pointing at our files.
            from libs import export
            export.configure_iptvsimple()

        elif params['action'] == 'list_settings':
            list_settings(params['label'])
        elif params['action'] == 'addon_settings':
            xbmcaddon.Addon().openSettings()
        elif params['action'] == 'reset_session':
           session = Session()
           session.remove_session()
        elif params['action'] == 'sign_in':
            # Drop the cached session so the next call really talks to the
            # API: if the device is already registered this just signs in
            # again, otherwise it asks how to register it.
            from libs.settings import Settings
            Settings().reset_json_data({'filename': 'session.txt', 'description': 'session'})
            Session()
            xbmcgui.Dialog().notification('Vodafone TV', 'Zařízení je přihlášené',
                                          xbmcgui.NOTIFICATION_INFO, 4000)
        elif params['action'] == 'reset_device_id':
            from libs.session import device_id, new_device_id
            current = device_id()
            choice = xbmcgui.Dialog().select(
                'Device ID: ' + current,
                ['Vygenerovat nové (nutná nová registrace zařízení)',
                 'Zadat ručně'])
            device = None
            if choice == 0:
                device = new_device_id()
            elif choice == 1:
                entered = xbmcgui.Dialog().input('Device ID', current)
                if entered and entered.strip() and entered.strip() != current:
                    device = new_device_id(entered)
            if device:
                xbmcgui.Dialog().notification('Vodafone TV', 'Device ID: ' + device,
                                              xbmcgui.NOTIFICATION_INFO, 6000)
        elif params['action'] == 'entitlement_probe':
            from libs.entitlement import probe
            probe()
        elif params['action'] == 'entitlement_reset':
            from libs.entitlement import forget_all
            forget_all()
            xbmcgui.Dialog().notification('Vodafone TV', 'Seznam nepředplacených kanálů byl vymazán', xbmcgui.NOTIFICATION_INFO, 4000)

        elif params['action'] == 'manage_channels':
            manage_channels(params['label'])
        elif params['action'] == 'reset_channels_list':
            channels = Channels()
            channels.reset_channels()   
        elif params['action'] == 'restore_channels':
            channels = Channels()
            channels.restore_channels(params['backup'])        
        elif params['action'] == 'list_channels_list_backups':
            list_channels_list_backups(params['label'])

        elif params['action'] == 'list_channels_edit':
            list_channels_edit(params['label'])
        elif params['action'] == 'edit_channel':
            edit_channel(params['id'])
        elif params['action'] == 'delete_channel':
            delete_channel(params['id'])
        elif params['action'] == 'change_channels_numbers':
            change_channels_numbers(params['from_number'], params['direction'])

        elif params['action'] == 'list_channels_groups':
            list_channels_groups(params['label'])
        elif params['action'] == 'add_channel_group':
            add_channel_group(params['label'])
        elif params['action'] == 'edit_channel_group':
            edit_channel_group(params['group'], params['label'])
        elif params['action'] == 'delete_channel_group':
            delete_channel_group(params['group'])
        elif params['action'] == 'select_channel_group':
            select_channel_group(params['group'])

        elif params['action'] == 'edit_channel_group_list_channels':
            edit_channel_group_list_channels(params['group'], params['label'])
        elif params['action'] == 'edit_channel_group_add_channel':
            edit_channel_group_add_channel(params['group'], params['channel'])
        elif params['action'] == 'edit_channel_group_add_all_channels':
            edit_channel_group_add_all_channels(params['group'])
        elif params['action'] == 'edit_channel_group_delete_channel':
            edit_channel_group_delete_channel(params['group'], params['channel'])

        else:
            raise ValueError('Neznámý parametr: {0}!'.format(paramstring))
    else:
        main_menu()

if __name__ == '__main__':
    router(sys.argv[2][1:])
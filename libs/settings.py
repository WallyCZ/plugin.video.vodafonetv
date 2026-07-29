# -*- coding: utf-8 -*-
import os
import sys
import xbmcaddon
import xbmcgui
import xbmcplugin
from xbmcvfs import translatePath

from libs.utils import get_url

def list_settings(label):
    _handle = int(sys.argv[1])
    xbmcplugin.setPluginCategory(_handle, label)

    list_item = xbmcgui.ListItem(label='Kanály')
    url = get_url(action='manage_channels', label = 'Kanály')
    xbmcplugin.addDirectoryItem(_handle, url, list_item, True)

    list_item = xbmcgui.ListItem(label='Nastavení doplňku')
    url = get_url(action='addon_settings', label = 'Nastavení doplňku')  
    xbmcplugin.addDirectoryItem(_handle, url, list_item, False)
    xbmcplugin.endOfDirectory(_handle)


class Settings:
    """Reads and writes the addon's own files under addon_data.

    These used to be gated on the credentials being filled in; there are no
    stored credentials any more (libs.session registers the device once and
    lives off its id), so there is nothing left to check.
    """

    def save_json_data(self, file, data):
        addon = xbmcaddon.Addon()
        addon_userdata_dir = translatePath(addon.getAddonInfo('profile'))
        filename = os.path.join(addon_userdata_dir, file['filename'])
        try:
            with open(filename, "w") as f:
                f.write('%s\n' % data)
        except IOError:
            xbmcgui.Dialog().notification('Vodafone TV', 'Chyba uložení ' + file['description'], xbmcgui.NOTIFICATION_ERROR, 5000)

    def load_json_data(self, file):
        data = None
        addon = xbmcaddon.Addon()
        addon_userdata_dir = translatePath(addon.getAddonInfo('profile'))
        filename = os.path.join(addon_userdata_dir, file['filename'])
        try:
            with open(filename, "r") as f:
                for row in f:
                    data = row[:-1]
        except IOError as error:
            if error.errno != 2:
                xbmcgui.Dialog().notification('Vodafone TV', 'Chyba při načtení ' + file['description'], xbmcgui.NOTIFICATION_ERROR, 5000)
        return data    

    def reset_json_data(self, file):
        addon = xbmcaddon.Addon()
        addon_userdata_dir = translatePath(addon.getAddonInfo('profile'))
        filename = os.path.join(addon_userdata_dir, file['filename'])
        if os.path.exists(filename):
            try:
                os.remove(filename) 
            except IOError:
                xbmcgui.Dialog().notification('Vodafone TV', 'Chyba při resetu ' + file['description'], xbmcgui.NOTIFICATION_ERROR, 5000)

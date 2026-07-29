# -*- coding: utf-8 -*-
import sys
import xbmc
import xbmcaddon
import xbmcgui

from urllib.parse import urlencode

plugin_id = 'plugin.video.vodafonetv'
day_translation = {'1' : 'Pondělí', '2' : 'Úterý', '3' : 'Středa', '4' : 'Čtvrtek', '5' : 'Pátek', '6' : 'Sobota', '0' : 'Neděle'}  
day_translation_short = {'1' : 'Po', '2' : 'Út', '3' : 'St', '4' : 'Čt', '5' : 'Pá', '6' : 'So', '0' : 'Ne'}  
clientTag = '9.40.0-PC'
apiVersion = '5.3.2'
partnerId = '3201'

_url = sys.argv[0]

def get_url(**kwargs):
    return '{0}?{1}'.format(_url, urlencode(kwargs))

def get_kodi_version():
    return int(xbmc.getInfoLabel('System.BuildVersion').split('.')[0])

def replace_by_html_entity(string):
    return string.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace("'","&apos;").replace('"',"&quot;")

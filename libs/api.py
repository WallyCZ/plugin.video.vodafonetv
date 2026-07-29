# -*- coding: utf-8 -*-
import xbmc
import xbmcaddon
import xbmcgui

import json
import gzip 

from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Cap on how long a single API request may take. Without one a stalled read
# hangs forever, or -- as seen on slower boxes -- eventually raises an uncaught
# TimeoutError that takes playback down. 30 s is generous for the normal
# responses while still bounding a dead connection.
API_TIMEOUT = 30

class API:
    def __init__(self):
        self.headers = {'User-Agent' : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0', 'Accept-Encoding' : 'gzip', 'Accept' : '*/*', 'Content-type' : 'application/json;charset=UTF-8'}

    def call_api(self, url, data, headers, nolog = False, sensitive = False):
        addon = xbmcaddon.Addon()
        if data is not None and isinstance(data, dict):
            data = json.dumps(data).encode("utf-8")
        request = Request(url = url , data = data, headers = headers)

        if addon.getSetting('log_request_url') == 'true':
            xbmc.log('Vodafone TV > ' + str(url))
        if addon.getSetting('log_request_url') == 'true' and data != None and sensitive == False:
            xbmc.log('Vodafone TV > ' + str(data))
        try:
            response = urlopen(request, timeout = API_TIMEOUT)
            if response.getheader("Content-Encoding") == 'gzip':
                gzipFile = gzip.GzipFile(fileobj = response)
                html = gzipFile.read()
            else:
                html = response.read()

            if 'vtv-sessionkey' in response.headers:
                self.session_key = json.loads(response.headers['vtv-sessionkey'])
            if addon.getSetting('log_response') == 'true' and nolog == False:
                xbmc.log('Vodafone TV > ' + str(html))
            if html and len(html) > 0:
                data = json.loads(html)
                return data
            else:
                return []
        except HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else 'No error body'
            xbmc.log('Vodafone TV > Chyba při volání ' + str(url) + ': ' + e.reason + ' - ' + error_body)
            return { 'err': e.reason, 'body': error_body }
        except (URLError, OSError) as e:
            # Network trouble (timeout, connection reset, DNS...). Return an
            # error dict like the HTTPError path instead of raising, so callers
            # can handle it or retry rather than crashing the whole script.
            reason = getattr(e, 'reason', e)
            xbmc.log('Vodafone TV > Síťová chyba při volání ' + str(url) + ': ' + str(reason),
                     xbmc.LOGWARNING)
            return { 'err': str(reason) }
        
def list_api(post, nolog = False, silent = False, retries = 2):
    result = []
    api = API()
    fetch = True
    while fetch == True:
        # Fetch one page, retrying a few times on a transient failure (e.g. a
        # read timeout on a slow connection) before giving up.
        attempt = 0
        while True:
            data = api.call_api(url = 'https://3062.vfp2.ott.kaltura.com/api_v3/service/asset/action/list', data = post, headers = api.headers, nolog = nolog)
            if isinstance(data, dict) and 'result' in data and 'totalCount' in data['result']:
                break
            if attempt < retries:
                attempt += 1
                xbmc.log('Vodafone TV > opakuji stažení dat (%d/%d)' % (attempt, retries),
                         xbmc.LOGWARNING)
                xbmc.sleep(1000)
                continue
            if silent == False:
                xbmcgui.Dialog().notification('Vodafone TV','Problém při stažení dat', xbmcgui.NOTIFICATION_ERROR, 5000)
            return result

        total_count = data['result']['totalCount']
        if total_count > 0:
            for object in data['result']['objects']:
                result.append(object)
            if total_count == len(result):
                fetch = False
            else:
                pager = post['pager']
                pager['pageIndex'] = pager['pageIndex'] + 1
                post['pager'] = pager
        else:
            fetch = False
    return result



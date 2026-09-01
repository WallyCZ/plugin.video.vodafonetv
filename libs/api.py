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

# Kaltura's way of saying "the session token you sent is not accepted any
# more". 500016 is the one seen in the wild ("KS expired"); 500015 is its
# invalid-token sibling. The clock is rarely the reason: the service throws
# every ks a device holds away the moment that device is removed in the
# Vodafone TV administration, so a session we still consider valid starts
# coming back as expired. libs.session.recover_expired turns that into a new
# login -- or, when the device really is gone, into a message that says so.
KS_ERROR_CODES = ('500015', '500016')

# Same failure spelled out in words, for the responses that carry no code.
KS_ERROR_TEXTS = ('ks expired', 'expired ks', 'invalid ks', 'ks not valid')


def find_api_error(data):
    """The API also reports failures inside 200 responses -- find those too.

    Shape: {"result": {"error": {"objectType":..., "code":..., "message":...}}}
    Returns the error dict, or None.
    """
    if isinstance(data, dict):
        error = data.get('error')
        if isinstance(error, dict) and ('code' in error or 'message' in error):
            return error
        for value in data.values():
            found = find_api_error(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_api_error(item)
            if found:
                return found
    return None


def is_ks_error(data):
    """True when a response failed because our ks is no longer accepted.

    Handles both shapes: the error inside a 200 body, and the JSON body of an
    HTTP error, which call_api hands back as {'err': ..., 'body': '<json>'}.
    """
    if not isinstance(data, dict):
        return False

    candidates = [data]
    body = data.get('body')
    if isinstance(body, str) and body:
        try:
            candidates.append(json.loads(body))
        except ValueError:
            candidates.append({'error': {'message': body}})

    for candidate in candidates:
        error = find_api_error(candidate)
        if not error:
            continue
        if str(error.get('code', '')) in KS_ERROR_CODES:
            return True
        message = str(error.get('message', '')).lower()
        if any(text in message for text in KS_ERROR_TEXTS):
            return True
    return False


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
    renewed = False
    while fetch == True:
        # Fetch one page, retrying a few times on a transient failure (e.g. a
        # read timeout on a slow connection) before giving up.
        attempt = 0
        while True:
            data = api.call_api(url = 'https://3062.vfp2.ott.kaltura.com/api_v3/service/asset/action/list', data = post, headers = api.headers, nolog = nolog)
            if isinstance(data, dict) and 'result' in data and 'totalCount' in data['result']:
                break
            if is_ks_error(data) and renewed == False:
                # The ks in the body is stale. Sign in again once and repeat the
                # page with the new one; retrying with the old ks never works.
                from libs.session import recover_expired
                renewed = True
                session = recover_expired(data, prompt = not silent)
                if session is not None:
                    post['ks'] = session.ks
                    continue
                return result
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



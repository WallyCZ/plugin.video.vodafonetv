# -*- coding: utf-8 -*-
"""The TV API gateway -- jsonpostgw.aspx.

Everything the Kaltura phoenix API does not cover lives here: full text
search, recordings, menus. One endpoint, dispatched on a `?m=<method>` query
parameter, with a shared `initObj` envelope carrying the session.

Leaving `?m=` off gets `{"Error": "Method Name does NOT included in Query
String.."}` -- an `Error` string with no status block, which reads exactly like
an empty result unless you look for it.
"""
import xbmc

URL = 'https://apigw.cz.vtv.vodafone.com/vtv/tvapi/v1/gateways/jsonpostgw.aspx?m=%s'

API_USER = 'tvpapi_3062'
API_PASS = 'jTe6ZmeBOedvBD'


class TvApiError(Exception):
    pass


DEFAULT_LOCALE = {'LocaleUserState': 'Unknown', 'LocaleCountry': 'null',
                  'LocaleLanguage': 'cs', 'LocaleDevice': 'null'}


def init_obj(session):
    from libs import dms
    from libs.session import device_id

    household, user = session.get_household()
    # the DMS config publishes these; the captured values are the fallback
    api_user, api_pass, platform = dms.init_obj_credentials(API_USER, API_PASS, 'Web')
    return {
        'ApiUser': api_user,
        'ApiPass': api_pass,
        'Platform': platform,
        'Locale': dms.init_obj_locale(DEFAULT_LOCALE),
        'DomainID': household,
        'SiteGuid': str(user),
        'Token': session.ks,
        'UDID': device_id(),
    }


def call(session, method, body = None, log_response = True):
    """POST to one gateway method. Returns the parsed response."""
    from libs.api import API
    from libs.widevine import redact

    from libs import dms

    api = API()
    post = dict(body or {})
    post['initObj'] = init_obj(session)

    gateway = dms.tvapi_gateway(None)
    url = ('%s?m=%s' % (gateway, method)) if gateway else (URL % method)

    headers = api.headers.copy()
    signed = session.sign(headers, post)
    xbmc.log('Vodafone TV TVAPI > %s %s'
             % (method, redact({k: v for k, v in post.items() if k != 'initObj'}, 60)),
             xbmc.LOGINFO)
    data = api.call_api(url = url, data = signed, headers = headers)

    if isinstance(data, dict):
        if 'err' in data:
            raise TvApiError('%s failed: %s' % (method, data.get('err')))
        if data.get('Error'):
            raise TvApiError('%s: %s' % (method, data['Error']))

        # `status` is not one shape: search answers with
        # {"code": 0, "message": ""} while the recording methods answer with
        # the plain string "OK" alongside a "msg".
        status = data.get('status')
        if isinstance(status, dict):
            if status.get('code'):
                raise TvApiError('%s returned %s %s' % (method, status.get('code'),
                                                        status.get('message')))
        elif isinstance(status, str) and status and status.upper() != 'OK':
            raise TvApiError('%s returned %s%s'
                             % (method, status,
                                ' (%s)' % data['msg'] if data.get('msg') else ''))
    if log_response:
        xbmc.log('Vodafone TV TVAPI > %s -> %s' % (method, redact(data, 60)[:600]),
                 xbmc.LOGINFO)
    return data

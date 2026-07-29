# -*- coding: utf-8 -*-
"""Which channels the household actually has a subscription for.

There is no entitlement flag in the data the addon already fetches:
`getPlaybackManifest` answers identically for a channel that plays (ČT 1 HD)
and one that does not (HBO HD) -- same `messages: [{code: OK}]`, same empty
`drm: []`, sources present in both. The refusal only appears at license time,
as `APIGWException apigw-50000`.

`productprice/action/list` does know, though. Given `fileIdIn` it answers with
one `KalturaPpvPrice` per media file carrying a `purchaseStatus`:

    {"objectType": "KalturaPpvPrice", "fileId": 109106494,
     "purchaseStatus": "subscription_purchased", "isSubscriptionOnly": true}

That is the primary check -- three calls cover all 133 channels and the result
is cached. A playback that still gets refused is remembered as well, so a
channel the price list calls purchasable but the license server rejects also
disappears.
"""
import json
import os
import time

import xbmc
import xbmcaddon
import xbmcvfs

from libs.utils import apiVersion

PHOENIX = 'https://apigw.cz.vtv.vodafone.com/vtv/phoenix/v1/api_v3/service/%s/action/%s'

# code the gateway answers with for a channel outside the subscription
NOT_ENTITLED_CODE = 'apigw-50000'

# Kaltura purchaseStatus values that mean "you cannot watch this". Anything
# else -- subscription_purchased, ppv_purchased, free, collection_purchased,
# the trial and pre-paid variants, or a value not seen before -- counts as
# entitled, so an unfamiliar status shows the channel rather than hiding one
# the household actually pays for.
NOT_ENTITLED_STATUSES = ('for_purchase', 'for_purchase_subscription_only')

# how many fileIds fit in one productprice/list call
CHUNK = 50

# how long the fetched statuses stay good
STATUS_TTL = 12 * 60 * 60


def log(message, level=xbmc.LOGINFO):
    xbmc.log('Vodafone TV ENT > ' + message, level)


def hide_enabled():
    """Hide channels known to be outside the subscription (default: yes)."""
    return xbmcaddon.Addon().getSetting('hide_unsubscribed') != 'false'


def _path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'unentitled.json')


def load():
    try:
        with open(_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save(data):
    try:
        directory = os.path.dirname(_path())
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
    except Exception as e:
        log('could not save the unentitled list: %s' % e, xbmc.LOGWARNING)


# ---------------------------------------------------------------------------
# purchaseStatus per media file (the real check)
# ---------------------------------------------------------------------------

def _status_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, 'entitlement.json')


def load_statuses():
    try:
        with open(_status_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        if int(time.time()) - data.get('ts', 0) > STATUS_TTL:
            return None
        return data.get('statuses') or None
    except Exception:
        return None


def _save_statuses(statuses):
    try:
        directory = os.path.dirname(_status_path())
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(_status_path(), 'w', encoding='utf-8') as f:
            json.dump({'ts': int(time.time()), 'statuses': statuses}, f, indent=1)
    except Exception as e:
        log('could not cache the purchase statuses: %s' % e, xbmc.LOGWARNING)


def fetch_statuses(file_ids):
    """{fileId: purchaseStatus} from productprice/list, in chunks."""
    from libs.api import API
    from libs.session import Session
    from libs.widevine import find_api_error, describe_api_error

    api, session = API(), Session()
    statuses = {}
    for start in range(0, len(file_ids), CHUNK):
        chunk = [str(f) for f in file_ids[start:start + CHUNK]]
        data = _call(api, session, 'productprice', 'list',
                     {'filter': {'objectType': 'KalturaProductPriceFilter',
                                 'fileIdIn': ','.join(chunk)}})
        error = find_api_error(data) if isinstance(data, dict) else None
        if error:
            raise RuntimeError('productprice/list: %s' % describe_api_error(error))
        for price in (data or {}).get('result', {}).get('objects', []):
            file_id = price.get('fileId')
            if file_id:
                statuses[str(file_id)] = price.get('purchaseStatus', '')
    return statuses


def refresh_statuses(file_ids, force=False):
    """Cached {fileId: purchaseStatus}; refetches when stale. Never raises."""
    if not force:
        cached = load_statuses()
        if cached:
            return cached
    try:
        statuses = fetch_statuses(file_ids)
    except Exception as e:
        log('could not read the subscription status (%s) -- showing all '
            'channels' % e, xbmc.LOGWARNING)
        return {}
    if statuses:
        _save_statuses(statuses)
        counts = {}
        for status in statuses.values():
            counts[status] = counts.get(status, 0) + 1
        log('purchase status for %d channels: %s'
            % (len(statuses), ', '.join('%s=%d' % kv for kv in sorted(counts.items()))))
    return statuses


def is_unentitled(channel_id, file_id=None, statuses=None):
    """True only when we positively know the channel cannot be watched."""
    if file_id is not None:
        if statuses is None:
            statuses = load_statuses() or {}
        status = statuses.get(str(file_id))
        if status in NOT_ENTITLED_STATUSES:
            return True
    return str(channel_id) in load()


def mark_unentitled(channel_id, name=None):
    data = load()
    key = str(channel_id)
    if key not in data:
        log('marking %s (%s) as not subscribed' % (key, name or '?'))
    data[key] = {'name': name, 'ts': int(time.time())}
    save(data)


def clear_unentitled(channel_id):
    """A channel that just played is obviously subscribed after all."""
    data = load()
    if data.pop(str(channel_id), None) is not None:
        log('%s plays again -- no longer marked as unsubscribed' % channel_id)
        save(data)


def forget_all():
    save({})
    try:
        os.remove(_status_path())
    except Exception:
        pass
    log('cleared the unsubscribed channel list and the cached statuses')


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def _call(api, session, service, action, body):
    body = dict(body)
    body.update({'apiVersion': apiVersion, 'ks': session.ks})
    headers = api.headers.copy()
    signed = session.sign(headers, body)
    return api.call_api(url=PHOENIX % (service, action), data=signed, headers=headers)


def probe():
    """Ask the entitlement endpoints what they know, and log the answers.

    Nothing here is relied on yet -- the point is to find out which call
    actually returns something usable on this account, so the learned-by-
    failure list can be replaced by an up-front check.
    """
    import xbmcgui
    from libs.api import API
    from libs.channels import Channels
    from libs.session import Session

    lines = []

    def report(text):
        log('[probe] ' + text)
        lines.append(text)

    api, session = API(), Session()
    channels = Channels().get_channels_list('id', visible_filter=False)
    file_ids = [str(c['fileId']) for c in channels.values() if c.get('fileId')]
    report('%d channels, %d with a fileId' % (len(channels), len(file_ids)))

    attempts = [
        ('entitlement', 'list',
         {'filter': {'objectType': 'KalturaEntitlementFilter',
                     'entitlementTypeEqual': 'subscription',
                     'entityReferenceEqual': 'household',
                     'isExpiredEqual': False},
          'pager': {'objectType': 'KalturaFilterPager', 'pageSize': 50,
                    'pageIndex': 1}}),
        ('entitlement', 'list',
         {'filter': {'objectType': 'KalturaEntitlementFilter',
                     'entitlementTypeEqual': 'ppv',
                     'entityReferenceEqual': 'household',
                     'isExpiredEqual': False},
          'pager': {'objectType': 'KalturaFilterPager', 'pageSize': 50,
                    'pageIndex': 1}}),
        # the canonical Kaltura "can this household play this file" call
        ('productprice', 'list',
         {'filter': {'objectType': 'KalturaProductPriceFilter',
                     'fileIdIn': ','.join(file_ids[:50])}}),
        ('subscription', 'list',
         {'filter': {'objectType': 'KalturaSubscriptionFilter'},
          'pager': {'objectType': 'KalturaFilterPager', 'pageSize': 50,
                    'pageIndex': 1}}),
    ]

    for service, action, body in attempts:
        label = '%s/%s %s' % (service, action,
                              body['filter'].get('entitlementTypeEqual', ''))
        try:
            data = _call(api, session, service, action, body)
        except Exception as e:
            report('%-28s -> errored: %s' % (label, e))
            continue
        report('%-28s -> %s' % (label, _summarise(data)))

    # What the addon will actually do with it
    report('')
    report('--- verdict per channel (productprice purchaseStatus) ---')
    try:
        statuses = refresh_statuses(file_ids, force=True)
    except Exception as e:
        statuses = {}
        report('could not fetch statuses: %s' % e)
    if statuses:
        counts = {}
        for status in statuses.values():
            counts[status] = counts.get(status, 0) + 1
        for status, n in sorted(counts.items()):
            verdict = 'HIDDEN' if status in NOT_ENTITLED_STATUSES else 'shown'
            report('  %-34s %4d channels  -> %s' % (status, n, verdict))
        missing = [c for c in channels.values()
                   if str(c.get('fileId')) not in statuses]
        if missing:
            report('  %-34s %4d channels  -> shown' % ('(no status returned)',
                                                       len(missing)))
        hidden = [c['name'] for c in channels.values()
                  if statuses.get(str(c.get('fileId'))) in NOT_ENTITLED_STATUSES]
        report('')
        report('would hide %d channel(s): %s'
               % (len(hidden), ', '.join(sorted(hidden)[:25]) or '-'))

    _save_report(lines)
    xbmcgui.Dialog().textviewer('Diagnostika předplatného', '\n'.join(lines))


def _summarise(data):
    from libs.widevine import find_api_error, describe_api_error, redact
    if isinstance(data, dict):
        error = find_api_error(data)
        if error:
            return 'ERROR ' + describe_api_error(error)
        result = data.get('result', data)
        if isinstance(result, dict) and 'objects' in result:
            objects = result['objects']
            summary = '%s, totalCount=%s' % (result.get('objectType'),
                                             result.get('totalCount'))
            if objects:
                summary += '\n      first object: ' + redact(objects[0], 60)[:700]
            return summary
        return redact(data, 60)[:500]
    return repr(data)[:300]


def _save_report(lines):
    try:
        profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
        if not os.path.isdir(profile):
            os.makedirs(profile)
        with open(os.path.join(profile, 'entitlement_probe.txt'), 'w',
                  encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except Exception as e:
        log('could not write the probe report: %s' % e, xbmc.LOGWARNING)

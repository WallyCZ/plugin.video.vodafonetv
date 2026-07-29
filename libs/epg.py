# -*- coding: utf-8 -*-
import xbmc
import time

from libs.session import Session
from libs.channels import Channels
from libs.api import list_api
from libs.utils import clientTag, apiVersion, get_kodi_version

def get_live_epg():
    session = Session()
    current_timestamp = int(time.time())
    post = {"language":"ces","ks":session.ks,"filter":{"objectType":"KalturaSearchAssetFilter","orderBy":"START_DATE_ASC","kSql":"(and start_date <= '" + str(current_timestamp) + "' end_date  >= '" + str(current_timestamp) + "' asset_type='epg' auto_fill= true)"},"pager":{"objectType":"KalturaFilterPager","pageSize":500,"pageIndex":1},"clientTag":clientTag,"apiVersion":apiVersion}
    return epg_api(post = post , key = 'channel_id')

def get_channel_live_epg(id):
    """The programme airing right now on a single channel, or None.

    play_live only needs the current show for the one channel being opened, so
    ask for just that -- a tiny response -- instead of get_live_epg's whole
    line-up, which is a large payload that times out on a slow connection and
    then takes playback down with it.
    """
    session = Session()
    now = int(time.time())
    post = {"language":"ces","ks":session.ks,"filter":{"objectType":"KalturaSearchAssetFilter","orderBy":"START_DATE_ASC","kSql":"(and linear_media_id:'" + str(id) + "' start_date <= '" + str(now) + "' end_date >= '" + str(now) + "' asset_type='epg' auto_fill= true)"},"pager":{"objectType":"KalturaFilterPager","pageSize":10,"pageIndex":1},"clientTag":clientTag,"apiVersion":apiVersion}
    epg = epg_api(post = post, key = 'channel_id')
    return epg.get(int(id))

def get_channel_epg(id, from_ts, to_ts):
    session = Session()
    post = {"language":"ces","ks":session.ks,"filter":{"objectType":"KalturaSearchAssetFilter","orderBy":"START_DATE_ASC","kSql":"(and linear_media_id:'" + str(id) + "' start_date >= '" + str(from_ts) + "' start_date  <= '" + str(to_ts) + "' asset_type='epg' auto_fill= true)"},"pager":{"objectType":"KalturaFilterPager","pageSize":500,"pageIndex":1},"clientTag":clientTag,"apiVersion":apiVersion}
    return epg_api(post = post, key = 'startts')

def get_series_epg(series_id, back_days = 7, forward_days = 7):
    """Every broadcast of one series across all channels, keyed by start time.

    This is how the web player expands a series search hit: a KalturaSearchAssetFilter
    on the series id (the field carries a space and comes in two casings, both
    OR'd), windowed to the catch-up range so the past airings are playable and a
    few upcoming ones can be recorded. Returns the same programme dicts epg_api
    builds for the archive, so each renders and plays exactly like an archive item.
    """
    session = Session()
    now = int(time.time())
    sid = str(series_id).replace("'", "")
    ksql = ("(and asset_type='epg' end_date>='%d' start_date<='%d' "
            "(or series ID='%s' Series ID='%s'))"
            % (now - back_days * 86400, now + forward_days * 86400, sid, sid))
    post = {"language":"ces","ks":session.ks,
            "filter":{"objectType":"KalturaSearchAssetFilter","orderBy":"START_DATE_DESC","kSql":ksql},
            "pager":{"objectType":"KalturaFilterPager","pageSize":500,"pageIndex":1},
            "clientTag":clientTag,"apiVersion":apiVersion}
    return epg_api(post = post, key = 'startts')
    
def epg_api(post, key):
    epg = {}
    result = list_api(post = post, nolog = True)
    channels = Channels()
    channels_list = channels.get_channels_list('id', visible_filter = False)            
    for item in result:
        if (item['objectType'] == 'KalturaProgramAsset' or item['objectType'] == 'KalturaRecordingAsset') and 'linearAssetId' in item:
            id = item['id']
            channel_id = item['linearAssetId']
            epgChannelId = item['epgChannelId']
            title = item['name']
            description = item['description']
            startts = item['startDate']
            endts = item['endDate']

            cover = ''
            poster = ''
            imdb = ''
            year = ''
            contentType = ''
            original = ''
            genres = []
            cast = []
            directors = []
            writers = []
            country = ''

            if len(item['images']) > 0:
                poster = item['images'][0]['url']
            if len(item['images']) > 1:
                cover = item['images'][1]['url']
            if 'original_name' in item['metas']:
                original = item['metas']['original_name']['value']
            if 'imdb_id' in item['metas']:
                imdb = str(item['metas']['imdb_id']['value'])
            if 'Year' in item['metas']:
                year = str(item['metas']['Year']['value'])
            if 'ContentType' in item['metas']:
                contentType = item['metas']['ContentType']['value']
            if 'Genre' in item['tags']:
                for genre in item['tags']['Genre']['objects']:
                    genres.append(genre['value'])
            if 'PersonReference' in item['tags']:
                for person in item['tags']['PersonReference']['objects']:
                    person_data = person['value'].split('|')
                    if len(person_data) < 3:
                        person_data.append('')
                    cast.append((person_data[1], person_data[2]))
            if 'Director' in item['tags']:
                for director in item['tags']['Director']['objects']:
                    directors.append(director['value'])
            if 'Writers' in item['tags']:
                for writer in item['tags']['Writers']['objects']:
                    writers.append(writer['value'])
            if 'Country' in item['tags'] and 'value' in item['tags']['Country']:
                country = item['tags']['Country']['value']

            episodeNumber = -1
            seasonNumber = -1
            episodesInSeason = -1
            episodeName = ''
            seasonName = ''
            seriesName = ''    

            if 'EpisodeNumber' in item['metas']:
                episodeNumber = int(item['metas']['EpisodeNumber']['value'])
            if 'SeasonNumber' in item['metas']:
                seasonNumber = int(item['metas']['SeasonNumber']['value'])
            if 'EpisodeInSeason' in item['metas']:
                episodesInSeason = int(item['metas']['EpisodeInSeason']['value'])
            if 'EpisodeName' in item['metas']:
                episodeName = str(item['metas']['EpisodeName']['value'])
            if 'SeasonName' in item['metas']:
                seasonName = str(item['metas']['SeasonName']['value'])
            if 'SeriesName' in item['metas']:
                seriesName = str(item['metas']['SeriesName']['value'])

            if 'IsSeries' in item['metas'] and int(item['metas']['IsSeries']['value']) == 1:
                isSeries = True
                seriesId = item['metas']['SeriesID']['value']
            else:
                isSeries = False
                seriesId = ''

            epg_item = {'id' : id, 'title' : title, 'channel_id' : channel_id, 'epgChannelId' : epgChannelId, 'description' : description, 'startts' : startts, 'endts' : endts, 'cover' : cover, 'poster' : poster, 'original' : original, 'imdb' : imdb, 'year' : year, 'contentType' : contentType, 'genres' : genres, 'cast' : cast, 'directors' : directors, 'writers' : writers, 'country' : country, 'episodeNumber' : episodeNumber, 'seasonNumber' : seasonNumber, 'episodesInSeason' : episodesInSeason, 'episodeName' : episodeName, 'seasonName' : seasonName, 'seriesName' : seriesName, 'isSeries' : isSeries, 'seriesId' : seriesId,}
            if key == 'startts':
                epg.update({startts : epg_item})
            elif key == 'channel_id':
                epg.update({channel_id : epg_item})
            elif key == 'id':
                epg.update({id : epg_item})
            elif key == 'startts_channel_number':
                if channel_id in channels_list:
                    epg.update({int(str(startts)+str(channels_list[channel_id]['channel_number']).zfill(5))  : epg_item})
    return epg

def epg_listitem(list_item, epg, logo):
    cast = []
    directors = []
    writers = []
    genres = []

    kodi_version = get_kodi_version()
    if kodi_version >= 20:
        infotag = list_item.getVideoInfoTag()
        infotag.setMediaType('movie')
    else:
        list_item.setInfo('video', {'mediatype' : 'movie'})
    if 'cover' in epg and len(epg['cover']) > 0:
        if 'poster' in epg and len(epg['poster']) > 0:
            list_item.setArt({'poster': epg['poster'], 'icon': epg['cover']})
        else:
            list_item.setArt({'thumb': epg['cover'], 'icon': epg['cover']})
    else:
        list_item.setArt({'thumb': logo, 'icon': logo})    
    if 'description' in epg and len(epg['description']) > 0:
        if kodi_version >= 20:
            infotag.setPlot(epg['description'])
        else:
            list_item.setInfo('video', {'plot': epg['description']})
    if 'imdb' in epg and len(epg['imdb']) > 0:
        if kodi_version >= 20:
            infotag.setIMDBNumber(epg['imdb'])
        else:
            list_item.setInfo('video', {'imdbnumber': epg['imdb']})
    if 'year' in epg and len(str(epg['year'])) > 0:
        if kodi_version >= 20:
            infotag.setYear(int(epg['year']))
        else:
            list_item.setInfo('video', {'year': int(epg['year'])})
    if 'original' in epg and len(epg['original']) > 0:
        if kodi_version >= 20:
            infotag.setOriginalTitle(epg['original'])
        else:
            list_item.setInfo('video', {'originaltitle': epg['original']})
    if 'country' in epg and len(epg['country']) > 0:
        if kodi_version >= 20:
            infotag.setCountries([epg['country']])
        else:
            list_item.setInfo('video', {'country': epg['country']})
    if 'genres' in epg and len(epg['genres']) > 0:
        for genre in epg['genres']:      
          genres.append(genre)
        if kodi_version >= 20:
            infotag.setGenres(genres)
        else:
            list_item.setInfo('video', {'genre' : genres})    
    if 'cast' in epg and len(epg['cast']) > 0:
        for person in epg['cast']: 
            if kodi_version >= 20:
                cast.append(xbmc.Actor(person[0], person[1]))
            else:
                cast.append(person)
        if kodi_version >= 20:
            infotag.setCast(cast)
        else:
            list_item.setInfo('video', {'castandrole' : cast})  
    if 'directors' in epg and len(epg['directors']) > 0:
        for person in epg['directors']:      
            directors.append(person)
        if kodi_version >= 20:
            infotag.setDirectors(directors)
        else:
            list_item.setInfo('video', {'director' : directors})  
    if 'writers' in epg and len(epg['writers']) > 0:
        for person in epg['writers']:      
            writers.append(person)
        if kodi_version >= 20:
            infotag.setWriters(writers)
        else:
            list_item.setInfo('video', {'writer' : writers})  
    if 'episodeNumber' in epg and epg['episodeNumber'] != None and int(epg['episodeNumber']) > 0:
        if kodi_version >= 20:
            infotag.setEpisode(int(epg['episodeNumber']))
        else:
            list_item.setInfo('video', {'mediatype': 'episode', 'episode' : int(epg['episodeNumber'])}) 
    if 'episodeName' in epg and epg['episodeName'] != None and len(epg['episodeName']) > 0:
        if kodi_version >= 20:
            infotag.setEpisodeGuide(epg['episodeName'])
        else:
            list_item.setInfo('video', {'title' : epg['episodeName']})  
    if 'seriesName' in epg and epg['seriesName'] != None and len(epg['seriesName']) > 0:
        if kodi_version >= 20:
            infotag.addSeason(int(epg['seasonNumber']), epg['seriesName'])
        else:
            list_item.setInfo('video', {'tvshowtitle' : epg['seriesName']})  
    if 'seasonNumber' in epg and epg['seasonNumber'] != None and int(epg['seasonNumber']) > 0:
        if kodi_version >= 20:
            infotag.setSeason(int(epg['seasonNumber']))
        else:
            list_item.setInfo('video', {'season' : int(epg['seasonNumber'])})  
    return list_item


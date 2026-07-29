import threading
import socket
import base64
import xbmc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs, urljoin
from urllib.request import urlopen, Request
import ssl
from xml.etree import ElementTree as ET
from io import BytesIO

from libs.session import Session
from libs.api import API
from libs.utils import apiVersion


def make_start_request(asset_id, program_id, file_id, session:Session, challenge):
    """The ccursession/start batch for a challenge InputStream Adaptive made.

    Only reachable with the addon's own CDM switched off (wv_isa_fallback):
    ISA never turns privacy mode on, so the licence server rejects whatever it
    produces. The proxy is not told which programme is playing, so the
    entitlement action carries no manifest window.
    """
    from libs.widevine import make_license_request

    entitlement_body = {
        'assetId': program_id,
        'assetType': 'epg',
        'contextDataParams': {
            'objectType': 'KalturaPlaybackContextOptions',
            'assetFileIds': file_id,
            'context': 'CATCHUP',
            'urlType': 'DIRECT',
        },
        'apiVersion': apiVersion,
        'ks': session.ks,
    }
    return make_license_request(session, entitlement_body, asset_id, program_id,
                                file_id, challenge)


class ProxyHandler(BaseHTTPRequestHandler):

    MPD_NS = '{urn:mpeg:dash:schema:mpd:2011}'

    def log_message(self, format, *args):
        # BaseHTTPRequestHandler logs every request to stderr, which Kodi
        # records at error level -- a flood, since ISA refetches the live
        # manifest every few seconds. Send it to our debug log instead.
        xbmc.log('Vodafone TV proxy > ' + (format % args), xbmc.LOGDEBUG)

    def patch_urls(self, element, base):
        """Make segment URLs absolute, honouring the DASH BaseURL chain.

        BaseURL may appear at MPD, Period, AdaptationSet and Representation
        level and each one is relative to the enclosing base. The unencrypted
        channels use a per-Representation `<BaseURL>./v360/</BaseURL>`, so
        resolving SegmentTemplate against the manifest directory alone drops
        that segment and every request 404s.

        The BaseURL elements themselves are rewritten to absolute too, so it
        does not matter whether the player applies them again to the (already
        absolute) segment URLs -- joining an absolute URL onto a base is a
        no-op.
        """
        for base_url in element.findall(self.MPD_NS + 'BaseURL'):
            if base_url.text and base_url.text.strip():
                base = urljoin(base, base_url.text.strip())
                base_url.text = base
                break  # multiple BaseURLs at one level are alternatives

        for segment_template in element.findall(self.MPD_NS + 'SegmentTemplate'):
            for attribute in ('initialization', 'media'):
                if attribute in segment_template.attrib:
                    segment_template.attrib[attribute] = urljoin(
                        base, segment_template.attrib[attribute])

        for child in element:
            if child.tag not in (self.MPD_NS + 'BaseURL',
                                 self.MPD_NS + 'SegmentTemplate'):
                self.patch_urls(child, base)

    def patch_manifest(self, manifest_data, manifest_url):
        """Patch relative URLs in the manifest to absolute URLs"""
        try:
            NAMESPACES = {
                None: 'urn:mpeg:dash:schema:mpd:2011',  # Default namespace (no prefix)
                'cenc': 'urn:mpeg:cenc:2013',
                'mspr': 'urn:microsoft:playready',
                'prm': 'urn:nagra:prm:1-0:services:schemas:mpd',
            }
            for prefix, uri in NAMESPACES.items():
                ET.register_namespace('' if prefix is None else prefix, uri)
            
            # Parse the XML manifest
            tree = ET.ElementTree(ET.fromstring(manifest_data))
            root = tree.getroot()

            # ISA does not support <UTCTiming> and warns that playback may
            # break. The stream plays fine without it (ISA falls back to its own
            # clock), so drop it to silence the warning and the potential issue.
            for utc_timing in root.findall(self.MPD_NS + 'UTCTiming'):
                root.remove(utc_timing)

            # Derive the base URL from the manifest URL (e.g., https://example.com/stream/)
            base_url = urljoin(manifest_url, '.')  # Gets the directory of the manifest URL

            # MPD@Location says where the manifest really lives, and relative
            # URLs resolve against that. The unencrypted channels return a
            # per-request session path there
            # (/channels/<id>/<uuid>/index.mpd), so resolving against the URL
            # we asked for instead produces segment URLs that all 404.
            location = root.find(self.MPD_NS + 'Location')
            if location is not None and location.text and location.text.strip():
                base_url = urljoin(urljoin(manifest_url, location.text.strip()), '.')

            self.patch_urls(root, base_url)

            # Serialize the modified XML
            output = BytesIO()
            tree.write(output, encoding='utf-8', xml_declaration=True)
            patched_manifest = output.getvalue()
            
            return patched_manifest
        except ET.ParseError:
            self.log_error('Failed to parse manifest XML')
            return manifest_data  # Return original if parsing fails
        except Exception as e:
            self.log_error(f'Error patching manifest: {e}')
            return manifest_data
    
    def do_GET(self):
        """Handle manifest requests"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path  # e.g., /myaddon/manifest
        query = parse_qs(parsed_path.query)  # e.g., {'url': ['https://.../original.manifest']}
        
        if not path.endswith('/manifest'):
            self.send_response(404)
            self.end_headers()
            return
        
        url = query.get('url', [None])[0]
        if not url:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'Missing url parameter')
            return
        
        try:
            context = ssl._create_unverified_context()
            # A plain urlopen sends "Python-urllib/x.y", which some CDNs reject
            # outright -- vodafone.streaming.tivio.studio (DVTV Extra and other
            # unencrypted channels) answers those with 403 while accepting any
            # ordinary user agent.
            from libs.widevine import USER_AGENT
            request = Request(unquote(url), headers={'User-Agent': USER_AGENT})
            with urlopen(request, timeout=10, context=context) as response:
                manifest_data = response.read()
                content_type = response.headers.get('content-type', 'application/dash+xml')
                # Relative URLs resolve against the URL the manifest was
                # finally served from, not the one we asked for. Some channels
                # redirect to an entirely different path
                # (/channels/<id>/index.mpd -> /v2/<a>/<b>/<uuid>/index.mpd),
                # and resolving against the request makes every segment 404.
                final_url = response.geturl()

            manifest_data = self.patch_manifest(manifest_data, final_url)

            self.send_response(200)
            self.send_header('content-type', content_type)
            self.end_headers()
            self.wfile.write(manifest_data)
        except Exception as e:
            # Without this the failure is invisible: Kodi only logs the proxy's
            # bare "500 -" line, not the reason.
            xbmc.log('Vodafone TV WV > manifest proxy failed for %s: %s: %s'
                     % (unquote(url), type(e).__name__, e), xbmc.LOGERROR)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def do_POST(self):
        """Handle license requests"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        query = parse_qs(parsed_path.query)
        
        if not path.endswith('/license'):
            self.send_response(404)
            self.end_headers()
            return
       
        try:
            # ISA posts "<challenge>!<session id>", both base64.
            length = int(self.headers.get('content-length', 0))
            bytes = self.rfile.read(length)
            challenge, sid = bytes.decode('utf-8').split('!')
            sid = base64.standard_b64decode(sid).decode('utf-8')

            api = API()
            session = Session()

            #'{BASE_URL}license?asset_id={asset_id}&program_id={program_id}&file_id={file_id}'
            asset_id = query.get('asset_id', [None])[0]
            program_id = query.get('program_id', [None])[0]
            file_id = query.get('file_id', [None])[0]

            headers = api.headers.copy()
            challenge_req = make_start_request(asset_id, program_id, file_id, session, challenge)
            # one bookSlot per start, otherwise Nagra answers 1007
            from libs.widevine import book_slot
            book_slot(session, api, asset_id, program_id, file_id)

            req_body = session.sign(headers, challenge_req)
            xbmc.log('Vodafone TV challenge body > ' + str(req_body))
            xbmc.log('Vodafone TV challenge body > ' + str(headers.get('vtv-authorization')))
            licence_data = api.call_api(url = 'https://apigw.cz.vtv.vodafone.com/vtv/ccursession/v1/start', data = req_body, headers = headers)

            # Call your method to do the magic to generate license data
            # The format type of data must be correct in according to your VOD service
            license_data = base64.standard_b64decode(licence_data['license'])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(license_data)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

class ProxyServer:
    def __init__(self, host='127.0.0.1', port=0):
        self.host = host
        self.port = port
        self.server = None
        self.thread = None
        self._initialize_server()

    def _initialize_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self.host, self.port))
            s.listen(1)
            self.port = s.getsockname()[1]
        
        # Threaded so a slow manifest fetch does not block the next request.
        # ISA refetches the live/timeshift manifest often, and a single-threaded
        # server serialises those behind any slow upstream fetch, leaving ISA on
        # a stale timeline (stalls, 504s, segment timeouts while shifting).
        self.server = ThreadingHTTPServer((self.host, self.port), ProxyHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True

    def start(self):
        if self.thread:
            self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def get_port(self):
        return self.port
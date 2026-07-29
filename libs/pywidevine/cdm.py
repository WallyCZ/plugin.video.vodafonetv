# -*- coding: utf-8 -*-
"""Widevine CDM: license challenge generation and license parsing.

Mirrors pywidevine's `Cdm`, minus the multi-session bookkeeping -- one
`Cdm` instance drives one license exchange, which is all a Kodi playback
needs.

Privacy mode (encrypted_client_id) is supported and is the whole point of
this module: InputStream Adaptive does not activate it (xbmc/inputstream.
adaptive#1850), and the Nagra SSP license server behind Vodafone TV
rejects challenges that carry a plaintext client_id.
"""
import time

try:
    from Cryptodome.Cipher import AES, PKCS1_OAEP
    from Cryptodome.Hash import CMAC, HMAC, SHA1, SHA256
    from Cryptodome.PublicKey import RSA
    from Cryptodome.Random import get_random_bytes
    from Cryptodome.Signature import pss
    from Cryptodome.Util.Padding import pad, unpad
except ImportError:                                     # pragma: no cover
    from Crypto.Cipher import AES, PKCS1_OAEP
    from Crypto.Hash import CMAC, HMAC, SHA1, SHA256
    from Crypto.PublicKey import RSA
    from Crypto.Random import get_random_bytes
    from Crypto.Signature import pss
    from Crypto.Util.Padding import pad, unpad

from libs.pywidevine import proto


class MessageType(object):
    LICENSE_REQUEST = 1
    LICENSE = 2
    ERROR_RESPONSE = 3
    SERVICE_CERTIFICATE_REQUEST = 4
    SERVICE_CERTIFICATE = 5


class LicenseType(object):
    STREAMING = 1
    OFFLINE = 2
    AUTOMATIC = 3


class RequestType(object):
    NEW = 1
    RENEWAL = 2
    RELEASE = 3


KEY_TYPES = {1: 'SIGNING', 2: 'CONTENT', 3: 'KEY_CONTROL',
             4: 'OPERATOR_SESSION', 5: 'ENTITLEMENT', 6: 'OEM_CONTENT'}

PROTOCOL_VERSION_2_1 = 21
PROTOCOL_VERSION_2_2 = 22

# SignedMessage{type=SERVICE_CERTIFICATE_REQUEST}, no payload
SERVICE_CERTIFICATE_CHALLENGE = proto.varint_field(1, MessageType.SERVICE_CERTIFICATE_REQUEST)


class Key(object):
    def __init__(self, kid, key, type_):
        self.kid = kid          # hex string
        self.key = key          # hex string
        self.type = KEY_TYPES.get(type_, str(type_))

    def __repr__(self):
        return '<Key %s %s:%s>' % (self.type, self.kid, self.key)


class ServiceCertificate(object):
    def __init__(self, provider_id, serial_number, public_key):
        self.provider_id = provider_id
        self.serial_number = serial_number
        self.public_key = public_key

    @classmethod
    def parse(cls, data):
        """Accept a SignedMessage, a SignedDrmCertificate or a DrmCertificate.

        The three are nested versions of each other, so unwrap until a
        DrmCertificate (the one carrying a public key) turns up.
        """
        cert_fields = cls._unwrap(data, 0)
        if cert_fields is None:
            raise ValueError('service certificate has no public key')
        return cls(proto.get_one(cert_fields, 7, default=b'').decode('utf-8', 'replace'),
                   proto.get_one(cert_fields, 2, default=b''),
                   proto.get_one(cert_fields, 4))

    @staticmethod
    def _is_drm_certificate(fields):
        # DrmCertificate{type=1 varint, serial_number=2, public_key=4}
        public_key = proto.get_one(fields, 4)
        return (public_key is not None and len(public_key) >= 64 and
                proto.get_one(fields, 1, proto.WIRE_VARINT) is not None)

    @classmethod
    def _unwrap(cls, data, depth):
        try:
            fields = proto.fields_dict(data)
        except Exception:
            return None
        if cls._is_drm_certificate(fields):
            return fields
        if depth >= 4:
            return None
        # SignedMessage{msg=2}, SignedDrmCertificate{drm_certificate=1}
        for field_no in (1, 2):
            nested = proto.get_one(fields, field_no)
            if nested:
                found = cls._unwrap(nested, depth + 1)
                if found is not None:
                    return found
        return None

    def __repr__(self):
        return '<ServiceCertificate provider_id=%s serial=%d B>' % (
            self.provider_id, len(self.serial_number))


class Cdm(object):
    def __init__(self, device):
        self.device = device
        self.service_certificate = None
        self.session_id = get_random_bytes(16)
        self._request_msg = None
        self._signed_data = None

    # ------------------------------------------------------------------
    # service certificate
    # ------------------------------------------------------------------

    def set_service_certificate(self, certificate):
        """certificate: raw bytes or base64 str. None clears privacy mode."""
        if certificate is None:
            self.service_certificate = None
            return None
        if isinstance(certificate, str):
            import base64
            s = certificate.strip().replace('-', '+').replace('_', '/')
            s += '=' * (-len(s) % 4)
            certificate = base64.b64decode(s)
        self.service_certificate = ServiceCertificate.parse(certificate)
        return self.service_certificate

    # ------------------------------------------------------------------
    # challenge
    # ------------------------------------------------------------------

    def get_license_challenge(self, init_data, license_type=LicenseType.STREAMING,
                              privacy_mode=True,
                              protocol_version=PROTOCOL_VERSION_2_1,
                              cdm_version=None, core_message=None,
                              sign_core_message=False):
        """Build a SignedMessage(LICENSE_REQUEST) for the given PSSH init_data.

        Returns raw bytes; base64-encode them for most license servers.

        The optional arguments exist because real CDMs send more than
        pywidevine does, and some license servers care:

        * `protocol_version` -- 2.1 like pywidevine, 2.2 like the captured
          Vodafone web player.
        * `cdm_version` -- LicenseRequest field 9, e.g. `16.1.1@014`; both
          captured challenges carry it, pywidevine never sends it.
        * `core_message` -- SignedMessage field 9, the OEMCrypto core message
          emitted by OEMCrypto v16+ devices.
        * `sign_core_message` -- sign `core_message + license_request` instead
          of the request alone.
        """
        # 16 raw bytes, the same shape the Chrome CDM uses
        request_id = self.session_id

        pssh_data = (proto.bytes_field(1, init_data) +
                     proto.varint_field(2, license_type) +
                     proto.bytes_field(3, request_id))
        content_id = proto.message_field(1, pssh_data)

        client_id = None
        encrypted_client_id = None
        if privacy_mode and self.service_certificate:
            encrypted_client_id = self._encrypt_client_id()
        else:
            client_id = self.device.client_id

        license_request = b''.join([
            proto.message_field(1, client_id),
            proto.message_field(2, content_id),
            proto.varint_field(3, RequestType.NEW),
            proto.varint_field(4, int(time.time())),
            proto.varint_field(6, protocol_version),
            proto.varint_field(7, _random_nonce()),
            proto.message_field(8, encrypted_client_id),
            proto.string_field(9, cdm_version),
        ])

        signed_data = license_request
        if core_message and sign_core_message:
            signed_data = core_message + license_request
        signature = pss.new(self.device.rsa_key).sign(SHA1.new(signed_data))

        self._request_msg = license_request
        self._signed_data = signed_data
        return (proto.varint_field(1, MessageType.LICENSE_REQUEST) +
                proto.bytes_field(2, license_request) +
                proto.bytes_field(3, signature) +
                proto.bytes_field(9, core_message))

    def _encrypt_client_id(self):
        cert = self.service_certificate
        privacy_key = get_random_bytes(16)
        privacy_iv = get_random_bytes(16)

        encrypted_client_id = AES.new(privacy_key, AES.MODE_CBC, privacy_iv).encrypt(
            pad(self.device.client_id, 16))
        encrypted_privacy_key = PKCS1_OAEP.new(RSA.importKey(cert.public_key)).encrypt(
            privacy_key)

        return b''.join([
            proto.string_field(1, cert.provider_id),
            proto.bytes_field(2, cert.serial_number),
            proto.bytes_field(3, encrypted_client_id),
            proto.bytes_field(4, privacy_iv),
            proto.bytes_field(5, encrypted_privacy_key),
        ])

    # ------------------------------------------------------------------
    # license
    # ------------------------------------------------------------------

    def parse_license(self, license_data):
        """Decrypt the content keys out of a license response.

        license_data: raw bytes (base64-decode the server response first).
        Returns a list of Key.
        """
        if self._request_msg is None:
            raise ValueError('parse_license called before get_license_challenge')
        if isinstance(license_data, str):
            import base64
            s = license_data.strip().replace('-', '+').replace('_', '/')
            s += '=' * (-len(s) % 4)
            license_data = base64.b64decode(s)

        signed = proto.fields_dict(license_data)
        msg_type = proto.get_one(signed, 1, proto.WIRE_VARINT)
        if msg_type == MessageType.ERROR_RESPONSE:
            raise ValueError('license server returned an ERROR_RESPONSE: %s'
                             % _hex(proto.get_one(signed, 2, default=b'')))
        if msg_type is not None and msg_type != MessageType.LICENSE:
            raise ValueError('expected a LICENSE message, got type %s' % msg_type)

        license_msg = proto.get_one(signed, 2)
        signature = proto.get_one(signed, 3)
        encrypted_session_key = proto.get_one(signed, 4)
        if not license_msg or not encrypted_session_key:
            raise ValueError('license response is missing the license or session key')

        session_key = PKCS1_OAEP.new(self.device.rsa_key).decrypt(encrypted_session_key)
        if len(session_key) != 16:
            raise ValueError('unexpected session key length %d' % len(session_key))

        # A core-message challenge may derive over `core_message + request`;
        # try both and keep whichever the server's HMAC agrees with.
        candidates = [self._request_msg]
        if self._signed_data and self._signed_data != self._request_msg:
            candidates.append(self._signed_data)

        enc_key = None
        for context_msg in candidates:
            enc_key, mac_key_server = self._derive_keys(session_key, context_msg)
            if not signature:
                break
            if HMAC.new(mac_key_server, license_msg, SHA256).digest() == signature:
                break
        else:
            raise ValueError('license signature mismatch (wrong device or '
                             'tampered response)')

        return self._decrypt_keys(license_msg, enc_key)

    def _derive_keys(self, session_key, msg):
        # key sizes as bit counts, big endian, exactly as OEMCrypto does it
        enc_context = b'ENCRYPTION\x00' + msg + b'\x00\x00\x00\x80'   # 128 bit
        mac_context = b'AUTHENTICATION\x00' + msg + b'\x00\x00\x02\x00'  # 2 x 256 bit

        def derive(context, counter):
            return CMAC.new(session_key, ciphermod=AES).update(
                bytes(bytearray([counter])) + context).digest()

        enc_key = derive(enc_context, 1)
        mac_key_server = derive(mac_context, 1) + derive(mac_context, 2)
        return enc_key, mac_key_server

    @staticmethod
    def _decrypt_keys(license_msg, enc_key):
        keys = []
        for container in proto.get_all(proto.fields_dict(license_msg), 3):
            key_fields = proto.fields_dict(container)
            kid = proto.get_one(key_fields, 1, default=b'')
            iv = proto.get_one(key_fields, 2)
            encrypted = proto.get_one(key_fields, 3)
            key_type = proto.get_one(key_fields, 4, proto.WIRE_VARINT, 0)
            if not iv or not encrypted:
                continue
            decrypted = unpad(AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted), 16)
            keys.append(Key(_hex(kid), _hex(decrypted), key_type))
        if not keys:
            raise ValueError('license contained no keys')
        return keys


def make_core_message():
    """An OEMCrypto core message shaped like the ones in the captures.

    Both the browser and the ISA challenge carry a 20 byte SignedMessage
    field 9 with the same fixed 12 byte head and 8 varying bytes:
        00000001 00000014 0005 0010 <8 bytes>
    """
    return (b'\x00\x00\x00\x01\x00\x00\x00\x14\x00\x05\x00\x10' +
            get_random_bytes(8))


def _random_nonce():
    return int(_hex(get_random_bytes(4)), 16) & 0x7FFFFFFF


def _hex(data):
    return ''.join('%02x' % b for b in bytearray(data))

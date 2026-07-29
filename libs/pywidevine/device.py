# -*- coding: utf-8 -*-
"""Widevine device (.wvd) file loading.

Layout produced by pywidevine (versions 1 and 2):

    b"WVD"          3 bytes  magic
    version         1 byte   1 or 2
    type            1 byte   1 = CHROME, 2 = ANDROID
    security_level  1 byte   1 (L1) .. 3 (L3)
    flags           1 byte   reserved
    private_key_len 2 bytes  big endian
    private_key     n bytes  RSA private key, DER (PKCS#1) or PEM
    client_id_len   2 bytes  big endian
    client_id       n bytes  serialized ClientIdentification protobuf
    vmp_len         2 bytes  (version 1 only)
    vmp             n bytes  (version 1 only)
"""
import os
import struct

MAGIC = b'WVD'

DEVICE_TYPES = {1: 'CHROME', 2: 'ANDROID'}


class Device(object):
    def __init__(self, type_, security_level, flags, private_key, client_id, vmp=None):
        self.type = type_
        self.type_name = DEVICE_TYPES.get(type_, 'UNKNOWN')
        self.security_level = security_level
        self.flags = flags
        self.private_key_data = private_key
        self.client_id = client_id
        self.vmp = vmp
        self._rsa_key = None
        self._client_info = None

    @property
    def client_info(self):
        """The device's ClientIdentification.client_info as a plain dict."""
        if self._client_info is None:
            from libs.pywidevine import proto
            info = {}
            try:
                for entry in proto.get_all(proto.fields_dict(self.client_id), 3):
                    pair = proto.fields_dict(entry)
                    name = proto.get_one(pair, 1, default=b'').decode('utf-8', 'replace')
                    value = proto.get_one(pair, 2, default=b'').decode('utf-8', 'replace')
                    if name:
                        info[name] = value
            except Exception:
                pass
            self._client_info = info
        return self._client_info

    @property
    def cdm_version(self):
        """e.g. '16.1.1@014' -- real CDMs put this in LicenseRequest field 9."""
        return self.client_info.get('widevine_cdm_version')

    @property
    def rsa_key(self):
        """The device RSA key, imported lazily so parsing stays cheap."""
        if self._rsa_key is None:
            try:
                from Cryptodome.PublicKey import RSA
            except ImportError:
                from Crypto.PublicKey import RSA
            self._rsa_key = RSA.importKey(self.private_key_data)
        return self._rsa_key

    @classmethod
    def loads(cls, data):
        if len(data) < 9 or data[:3] != MAGIC:
            raise ValueError('not a .wvd file (bad magic)')

        version = data[3]
        if version not in (1, 2):
            raise ValueError('unsupported .wvd version %d' % version)

        type_, security_level, flags = data[4], data[5], data[6]
        offset = 7

        def read_block(off):
            if off + 2 > len(data):
                raise ValueError('truncated .wvd file')
            (length,) = struct.unpack_from('>H', data, off)
            off += 2
            if off + length > len(data):
                raise ValueError('truncated .wvd file')
            return data[off:off + length], off + length

        private_key, offset = read_block(offset)
        client_id, offset = read_block(offset)

        vmp = None
        if version == 1 and offset + 2 <= len(data):
            vmp, offset = read_block(offset)

        if not private_key or not client_id:
            raise ValueError('.wvd file is missing the private key or client id')

        return cls(type_, security_level, flags, private_key, client_id, vmp)

    @classmethod
    def load(cls, path):
        with open(path, 'rb') as f:
            return cls.loads(f.read())

    def describe(self):
        return 'type=%s security_level=L%s private_key=%d B client_id=%d B' % (
            self.type_name, self.security_level,
            len(self.private_key_data), len(self.client_id))

    def __repr__(self):
        return '<Device %s>' % self.describe()


def find_wvd(*directories):
    """Return the first *.wvd found in the given directories, or None."""
    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if name.lower().endswith('.wvd'):
                return os.path.join(directory, name)
    return None

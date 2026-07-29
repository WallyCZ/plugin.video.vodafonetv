# -*- coding: utf-8 -*-
"""PSSH box handling.

The license challenge carries `init_data` -- the payload of the Widevine
PSSH box (a WidevinePsshData protobuf), not the box itself. This module
unwraps a box when given one and can synthesise init_data from a bare KID
for manifests that only expose `cenc:default_KID`.
"""
import base64
import struct

from libs.pywidevine import proto

WIDEVINE_SYSTEM_ID = bytes(bytearray.fromhex('edef8ba979d64acea3c827dcd51d21ed'))

# WidevinePsshData
_ALGORITHM = 1      # 1 = AESCTR
_KEY_IDS = 2


class PSSH(object):
    def __init__(self, init_data, key_ids=None, box=None):
        self.init_data = init_data
        self.key_ids = key_ids or []
        self.box = box

    @classmethod
    def from_bytes(cls, data):
        """Accept a full PSSH box or an already-unwrapped WidevinePsshData."""
        if len(data) > 12 and data[4:8] == b'pssh':
            return cls._from_box(data)
        # not a box -- assume it is already init_data
        return cls(data, _key_ids_of(data))

    @classmethod
    def from_b64(cls, b64):
        s = b64.strip().replace('-', '+').replace('_', '/')
        s += '=' * (-len(s) % 4)
        return cls.from_bytes(base64.b64decode(s))

    @classmethod
    def from_key_id(cls, key_id):
        """Build init_data for a KID (16 raw bytes or a hex/dashed string)."""
        kid = _normalise_kid(key_id)
        init_data = (proto.varint_field(_ALGORITHM, 1) +
                     proto.bytes_field(_KEY_IDS, kid))
        return cls(init_data, [kid])

    @classmethod
    def _from_box(cls, box):
        (size,) = struct.unpack_from('>I', box, 0)
        if size and size <= len(box):
            box = box[:size]
        version = box[8]
        system_id = box[12:28]
        if system_id != WIDEVINE_SYSTEM_ID:
            raise ValueError('PSSH box is not Widevine (system id %s)'
                             % _hex(system_id))
        offset = 28
        key_ids = []
        if version > 0:
            (kid_count,) = struct.unpack_from('>I', box, offset)
            offset += 4
            for _ in range(kid_count):
                key_ids.append(box[offset:offset + 16])
                offset += 16
        (data_size,) = struct.unpack_from('>I', box, offset)
        offset += 4
        init_data = box[offset:offset + data_size]
        return cls(init_data, key_ids or _key_ids_of(init_data), box)

    def as_box(self):
        """Serialize back to a version 0 PSSH box."""
        if self.box:
            return self.box
        payload = (b'pssh' + b'\x00\x00\x00\x00' + WIDEVINE_SYSTEM_ID +
                   struct.pack('>I', len(self.init_data)) + self.init_data)
        return struct.pack('>I', len(payload) + 4) + payload

    def __repr__(self):
        return '<PSSH init_data=%d B kids=%s>' % (
            len(self.init_data), [_hex(k) for k in self.key_ids])


def _key_ids_of(init_data):
    try:
        fields = proto.fields_dict(init_data)
    except Exception:
        return []
    return [k for k in proto.get_all(fields, _KEY_IDS) if len(k) == 16]


def _normalise_kid(key_id):
    if isinstance(key_id, (bytes, bytearray)):
        if len(key_id) == 16:
            return bytes(key_id)
        key_id = key_id.decode('utf-8')
    key_id = key_id.replace('-', '').strip()
    return bytes(bytearray.fromhex(key_id))


def _hex(data):
    return ''.join('%02x' % b for b in bytearray(data))

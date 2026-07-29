# -*- coding: utf-8 -*-
"""Minimal protobuf wire-format codec.

Only what the Widevine license protocol needs: varints, length-delimited
fields and nested messages. This keeps the ~1 MB google.protobuf runtime
(and the generated license_protocol_pb2) out of the addon -- the Widevine
messages we build and read are small and their field numbers are stable.
"""

WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_BYTES = 2
WIRE_32BIT = 5


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------

def encode_varint(value):
    value = int(value)
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field_no, wire):
    return encode_varint((field_no << 3) | wire)


def varint_field(field_no, value):
    """Encode a varint field. `None` encodes to nothing (unset optional)."""
    if value is None:
        return b''
    if isinstance(value, bool):
        value = 1 if value else 0
    return _tag(field_no, WIRE_VARINT) + encode_varint(value)


def bytes_field(field_no, value):
    """Encode a length-delimited field (bytes, str or nested message)."""
    if value is None:
        return b''
    if isinstance(value, str):
        value = value.encode('utf-8')
    return _tag(field_no, WIRE_BYTES) + encode_varint(len(value)) + bytes(value)


# nested messages and strings are the same wire type, aliased for readability
message_field = bytes_field
string_field = bytes_field


# --------------------------------------------------------------------------
# decoding
# --------------------------------------------------------------------------

def read_varint(buf, i):
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def iter_fields(buf):
    """Yield (field_no, wire_type, value) for a serialized message.

    `value` is an int for varints and raw bytes for everything else.
    Raises ValueError on malformed input.
    """
    i, n = 0, len(buf)
    while i < n:
        key, i = read_varint(buf, i)
        field_no, wire = key >> 3, key & 7
        if wire == WIRE_VARINT:
            val, i = read_varint(buf, i)
            yield field_no, wire, val
        elif wire == WIRE_BYTES:
            ln, i = read_varint(buf, i)
            yield field_no, wire, bytes(buf[i:i + ln])
            i += ln
        elif wire == WIRE_32BIT:
            yield field_no, wire, bytes(buf[i:i + 4])
            i += 4
        elif wire == WIRE_64BIT:
            yield field_no, wire, bytes(buf[i:i + 8])
            i += 8
        else:
            raise ValueError('unknown protobuf wire type %d at offset %d' % (wire, i))


def fields_dict(buf):
    """Parse into {field_no: [(wire_type, value), ...]} keeping repeats."""
    out = {}
    for field_no, wire, val in iter_fields(buf):
        out.setdefault(field_no, []).append((wire, val))
    return out


def get_one(fields, field_no, wire=WIRE_BYTES, default=None):
    """First value of `field_no`, or `default` if absent / wrong wire type."""
    entries = fields.get(field_no)
    if not entries or entries[0][0] != wire:
        return default
    return entries[0][1]


def get_all(fields, field_no, wire=WIRE_BYTES):
    """All values of a repeated field."""
    return [val for w, val in fields.get(field_no, []) if w == wire]

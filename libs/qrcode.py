# -*- coding: utf-8 -*-
"""A small QR encoder, enough for the pairing deep link.

Kodi ships no QR library and neither does any addon here, and the payload
carries a login pin, so handing it to an online QR service is out of the
question. This does byte mode, error correction level M, versions 1 to 10 --
the pairing URL is 81 bytes and lands in version 5.

Written from ISO/IEC 18004. The tests round-trip an encoded symbol back
through a reader and check the Reed-Solomon syndromes, which is what keeps
this honest.
"""
import struct
import zlib

# (total codewords, ec codewords per block, (blocks, data per block) groups)
# for error correction level M.
VERSIONS = {
    1:  (26,  10, ((1, 16),)),
    2:  (44,  16, ((1, 28),)),
    3:  (70,  26, ((1, 44),)),
    4:  (100, 18, ((2, 32),)),
    5:  (134, 24, ((2, 43),)),
    6:  (172, 16, ((4, 27),)),
    7:  (196, 18, ((4, 31),)),
    8:  (242, 22, ((2, 38), (2, 39))),
    9:  (292, 22, ((3, 36), (2, 37))),
    10: (346, 26, ((4, 43), (1, 44))),
}

ALIGNMENT = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50],
}

# bits left over after the codewords, per version
REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7,
                  7: 0, 8: 0, 9: 0, 10: 0}

ECC_LEVEL_M = 0b00  # the two format bits for level M


# ---------------------------------------------------------------------------
# GF(256), the field Reed-Solomon works in
# ---------------------------------------------------------------------------

EXP = [0] * 512
LOG = [0] * 256


def _build_tables():
    x = 1
    for i in range(255):
        EXP[i] = x
        LOG[x] = i
        x <<= 1
        if x & 0x100:          # x^8 = x^4 + x^3 + x^2 + 1
            x ^= 0x11D
    for i in range(255, 512):
        EXP[i] = EXP[i - 255]


_build_tables()


def gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def poly_mul(a, b):
    out = [0] * (len(a) + len(b) - 1)
    for i, ca in enumerate(a):
        for j, cb in enumerate(b):
            out[i + j] ^= gf_mul(ca, cb)
    return out


def rs_generator(degree):
    """(x - a^0)(x - a^1)...(x - a^(degree-1)), coefficients high to low."""
    poly = [1]
    for i in range(degree):
        poly = poly_mul(poly, [1, EXP[i]])
    return poly


def rs_remainder(data, degree):
    """The `degree` error correction codewords for `data`."""
    generator = rs_generator(degree)
    remainder = list(data) + [0] * degree
    for i in range(len(data)):
        factor = remainder[i]
        if factor:
            for j, coefficient in enumerate(generator):
                remainder[i + j] ^= gf_mul(coefficient, factor)
    return remainder[len(data):]


def rs_syndromes(codewords, degree):
    """Zero for every valid codeword -- used by the tests, not by the encoder."""
    out = []
    for i in range(degree):
        value = 0
        for coefficient in codewords:
            value = gf_mul(value, EXP[i]) ^ coefficient
        out.append(value)
    return out


# ---------------------------------------------------------------------------
# bits -> codewords
# ---------------------------------------------------------------------------

class Bits(object):
    def __init__(self):
        self.bits = []

    def add(self, value, length):
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self):
        return len(self.bits)


def pick_version(length):
    for version in sorted(VERSIONS):
        count_bits = 8 if version < 10 else 16
        capacity = data_codewords(version) * 8
        if 4 + count_bits + length * 8 <= capacity:
            return version
    raise ValueError('%d bytes is too long for this encoder' % length)


def data_codewords(version):
    total = 0
    for blocks, per_block in VERSIONS[version][2]:
        total += blocks * per_block
    return total


def encode_data(data, version):
    """Mode, length, payload, padding -- the data codewords before RS."""
    bits = Bits()
    bits.add(0b0100, 4)                                   # byte mode
    bits.add(len(data), 8 if version < 10 else 16)
    for byte in bytearray(data):
        bits.add(byte, 8)

    capacity = data_codewords(version) * 8
    bits.add(0, min(4, capacity - len(bits)))             # terminator
    if len(bits) % 8:
        bits.add(0, 8 - len(bits) % 8)

    codewords = []
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits.bits[i:i + 8]:
            byte = (byte << 1) | bit
        codewords.append(byte)

    for i in range(data_codewords(version) - len(codewords)):
        codewords.append(0xEC if i % 2 == 0 else 0x11)    # pad codewords
    return codewords


def interleave(codewords, version):
    """Split into blocks, add RS to each, then interleave as the spec wants."""
    _, ec_per_block, groups = VERSIONS[version]

    blocks = []
    offset = 0
    for count, per_block in groups:
        for _ in range(count):
            blocks.append(codewords[offset:offset + per_block])
            offset += per_block

    ec_blocks = [rs_remainder(block, ec_per_block) for block in blocks]

    out = []
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            out.append(block[i])
    return out


# ---------------------------------------------------------------------------
# the symbol
# ---------------------------------------------------------------------------

def size_of(version):
    return version * 4 + 17


def new_matrix(version):
    """(modules, reserved) -- reserved marks everything that is not data."""
    size = size_of(version)
    modules = [[0] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def finder(row, column):
        for r in range(-1, 8):
            for c in range(-1, 8):
                if not (0 <= row + r < size and 0 <= column + c < size):
                    continue
                inside = (0 <= r < 7 and 0 <= c < 7)
                dark = inside and (r in (0, 6) or c in (0, 6) or
                                   (2 <= r <= 4 and 2 <= c <= 4))
                modules[row + r][column + c] = 1 if dark else 0
                reserved[row + r][column + c] = True

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for i in range(8, size - 8):                          # timing patterns
        bit = 1 if i % 2 == 0 else 0
        modules[6][i] = bit
        modules[i][6] = bit
        reserved[6][i] = True
        reserved[i][6] = True

    centres = ALIGNMENT[version]
    last = len(centres) - 1
    for i, row in enumerate(centres):
        for j, column in enumerate(centres):
            # Only the three that would sit on a finder are left out. Testing
            # "is the centre already reserved" instead also drops the ones
            # centred on a timing line, which the spec does place -- that is
            # wrong from version 7 up, where the middle centre exists.
            if (i, j) in ((0, 0), (0, last), (last, 0)):
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    dark = max(abs(r), abs(c)) != 1
                    modules[row + r][column + c] = 1 if dark else 0
                    reserved[row + r][column + c] = True

    for i in range(9):                                    # format information
        if not reserved[8][i]:
            reserved[8][i] = True
        if not reserved[i][8]:
            reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True
    modules[size - 8][8] = 1                              # always dark
    reserved[size - 8][8] = True

    if version >= 7:                                      # version information
        for i in range(6):
            for j in range(3):
                reserved[size - 11 + j][i] = True
                reserved[i][size - 11 + j] = True
    return modules, reserved


def place_data(modules, reserved, codewords, version):
    size = size_of(version)
    bits = []
    for codeword in codewords:
        for i in range(7, -1, -1):
            bits.append((codeword >> i) & 1)
    bits += [0] * REMAINDER_BITS[version]

    index = 0
    column = size - 1
    upward = True
    while column > 0:
        if column == 6:                                   # skip the timing column
            column -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (column, column - 1):
                if reserved[row][c]:
                    continue
                modules[row][c] = bits[index] if index < len(bits) else 0
                index += 1
        column -= 2
        upward = not upward


MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def apply_mask(modules, reserved, mask):
    size = len(modules)
    out = [row[:] for row in modules]
    for row in range(size):
        for column in range(size):
            if not reserved[row][column] and MASKS[mask](row, column):
                out[row][column] ^= 1
    return out


def penalty(modules):
    size = len(modules)
    score = 0

    for line in list(modules) + [list(column) for column in zip(*modules)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)

    for row in range(size - 1):
        for column in range(size - 1):
            block = (modules[row][column] + modules[row][column + 1] +
                     modules[row + 1][column] + modules[row + 1][column + 1])
            if block in (0, 4):
                score += 3

    pattern = [1, 0, 1, 1, 1, 0, 1]
    for line in list(modules) + [list(column) for column in zip(*modules)]:
        for i in range(size - 6):
            if line[i:i + 7] != pattern:
                continue
            before = line[max(0, i - 4):i]
            after = line[i + 7:i + 11]
            if len(before) == 4 and sum(before) == 0:
                score += 40
            if len(after) == 4 and sum(after) == 0:
                score += 40

    dark = sum(sum(row) for row in modules)
    percent = dark * 100 // (size * size)
    score += 10 * min(abs(percent - 50) // 5, abs(percent - 50 + 4) // 5)
    return score


def format_bits(mask, level = ECC_LEVEL_M):
    value = (level << 3) | mask
    remainder = value << 10
    for i in range(4, -1, -1):
        if remainder & (1 << (i + 10)):
            remainder ^= 0x537 << i
    return ((value << 10) | remainder) ^ 0x5412


def version_bits(version):
    remainder = version << 12
    for i in range(5, -1, -1):
        if remainder & (1 << (i + 12)):
            remainder ^= 0x1F25 << i
    return (version << 12) | remainder


def place_format(modules, mask, version):
    """Both copies of the 15 format bits, in (row, column) order.

    Watch the orientation: the copy around the top-left finder runs *down
    column 8* for bits 0..5 and then *along row 8* for 9..14, not the other
    way round. Transposing it is invisible to anything that reads the symbol
    back the same way, and fatal to a real scanner -- it finds no valid
    format information and gives up before it ever looks at the data.
    """
    size = len(modules)
    bits = format_bits(mask)
    for i in range(15):
        bit = (bits >> i) & 1

        if i < 6:
            modules[i][8] = bit
        elif i == 6:
            modules[7][8] = bit
        elif i == 7:
            modules[8][8] = bit
        elif i == 8:
            modules[8][7] = bit
        else:
            modules[8][14 - i] = bit

        # the second copy: bits 0..7 along row 8 on the right, 8..14 down
        # column 8 at the bottom
        if i < 8:
            modules[8][size - 1 - i] = bit
        else:
            modules[size - 15 + i][8] = bit

    modules[size - 8][8] = 1                              # always dark

    if version >= 7:
        bits = version_bits(version)
        for i in range(18):
            bit = (bits >> i) & 1
            row, column = i // 3, i % 3
            modules[size - 11 + column][row] = bit
            modules[row][size - 11 + column] = bit


def encode(text):
    """The QR modules for `text`, as a list of rows of 0/1."""
    data = text.encode('utf-8') if not isinstance(text, bytes) else text
    version = pick_version(len(data))
    codewords = interleave(encode_data(data, version), version)

    modules, reserved = new_matrix(version)
    place_data(modules, reserved, codewords, version)

    best = None
    for mask in range(8):
        candidate = apply_mask(modules, reserved, mask)
        place_format(candidate, mask, version)
        score = penalty(candidate)
        if best is None or score < best[0]:
            best = (score, candidate)
    return best[1]


# ---------------------------------------------------------------------------
# PNG, so Kodi can show it
# ---------------------------------------------------------------------------

def _write_png(path, width, height, raw, colour = False):
    def chunk(tag, payload):
        return (struct.pack('>I', len(payload)) + tag + payload +
                struct.pack('>I', zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack('>IIBBBBB', width, height, 8, 2 if colour else 0,
                         0, 0, 0)
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(data)
    return path


def png(modules, path, scale = None, quiet = 4, target = 1000, invert = False):
    """Write the symbol as a PNG.

    The quiet zone is not decoration: scanners need four clear modules around
    the symbol to find it at all.

    By default the image is drawn at roughly `target` pixels across. Kodi
    scales whatever it is given to the control, and a smoothed upscale rounds
    the module corners off enough that a phone camera can stop resolving them
    -- so draw it larger than it will ever be shown and let Kodi scale down.

    `invert` swaps the colours -- light modules on a dark field, quiet zone
    included. The Vodafone mobile app's pairing scanner reads only inverted
    codes (matching the TV app, which renders them that way); a standard black
    on white symbol, though valid to every generic reader, it ignores.
    """
    size = len(modules)
    if scale is None:
        scale = max(8, target // (size + quiet * 2))
    width = (size + quiet * 2) * scale
    dark_px, light_px = (255, 0) if invert else (0, 255)

    rows = []
    for row in range(size + quiet * 2):
        line = bytearray()
        line.append(0)                                    # filter: none
        for column in range(size + quiet * 2):
            inside = (quiet <= row < size + quiet and quiet <= column < size + quiet)
            dark = inside and modules[row - quiet][column - quiet]
            line += bytearray([dark_px if dark else light_px] * scale)
        rows += [bytes(line)] * scale
    return _write_png(path, width, width, b''.join(rows))


def solid_png(path, rgb = (16, 16, 16), size = 8):
    """A plain block of colour, to back a window with.

    Kodi has no "just fill this rectangle" control and skin textures are not
    ours to rely on, so the background is a tiny image the ControlImage
    stretches.
    """
    row = bytes([0]) + bytes(rgb) * size
    return _write_png(path, size, size, row * size, colour = True)

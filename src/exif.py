"""Minimal EXIF writer for JPEG, and a PNG text-chunk writer.

ffmpeg cannot author EXIF: the mjpeg encoder drops ``-metadata`` on the floor,
so the camera identity has to be written into the file after the encode. This
module builds an APP1 segment from scratch rather than pulling in piexif,
because the app currently ships exactly one runtime dependency (PyQt6) and
``fingerprint.py`` already sets the precedent of doing the fiddly bit by hand
instead of taking a library into the bundle.

Only the write path exists. Nothing here reads or preserves an input file's
EXIF — the whole point is that the source's metadata is discarded and a
fabricated camera identity replaces it.
"""

import struct
from typing import Dict, List, Optional, Sequence, Tuple

# TIFF field types used here.
BYTE = 1
ASCII = 2
SHORT = 3
LONG = 4
RATIONAL = 5
UNDEFINED = 7

_TYPE_SIZE = {BYTE: 1, ASCII: 1, SHORT: 2, LONG: 4, RATIONAL: 8, UNDEFINED: 1}

# IFD0
TAG_ORIENTATION = 0x0112
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_SOFTWARE = 0x0131
TAG_DATETIME = 0x0132
TAG_X_RESOLUTION = 0x011A
TAG_Y_RESOLUTION = 0x011B
TAG_RESOLUTION_UNIT = 0x0128
TAG_YCBCR_POSITIONING = 0x0213
TAG_EXIF_IFD = 0x8769
TAG_GPS_IFD = 0x8825

# Exif IFD
TAG_EXPOSURE_TIME = 0x829A
TAG_F_NUMBER = 0x829D
TAG_EXPOSURE_PROGRAM = 0x8822
TAG_ISO = 0x8827
TAG_EXIF_VERSION = 0x9000
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_SHUTTER_SPEED = 0x9201
TAG_APERTURE = 0x9202
TAG_EXPOSURE_BIAS = 0x9204
TAG_METERING_MODE = 0x9207
TAG_FLASH = 0x9209
TAG_FOCAL_LENGTH = 0x920A
TAG_SUBSEC_ORIGINAL = 0x9291
TAG_COLOR_SPACE = 0xA001
TAG_PIXEL_X = 0xA002
TAG_PIXEL_Y = 0xA003
TAG_WHITE_BALANCE = 0xA403
TAG_FOCAL_35MM = 0xA405
TAG_LENS_MAKE = 0xA433
TAG_LENS_MODEL = 0xA434

# GPS IFD
TAG_GPS_VERSION = 0x0000
TAG_GPS_LAT_REF = 0x0001
TAG_GPS_LAT = 0x0002
TAG_GPS_LON_REF = 0x0003
TAG_GPS_LON = 0x0004
TAG_GPS_ALT_REF = 0x0005
TAG_GPS_ALT = 0x0006

Entry = Tuple[int, int, object]      # (tag, type, value)


def _encode_value(typ: int, value) -> Tuple[int, bytes]:
    """Return (component count, payload bytes) for one field value."""
    if typ == ASCII:
        raw = str(value).encode("ascii", errors="replace") + b"\x00"
        return len(raw), raw
    if typ in (BYTE, UNDEFINED):
        raw = bytes(value) if not isinstance(value, bytes) else value
        return len(raw), raw
    if typ == SHORT:
        items = value if isinstance(value, (list, tuple)) else [value]
        return len(items), b"".join(struct.pack(">H", int(v)) for v in items)
    if typ == LONG:
        items = value if isinstance(value, (list, tuple)) else [value]
        return len(items), b"".join(struct.pack(">I", int(v)) for v in items)
    if typ == RATIONAL:
        # A single rational is a (num, den) pair; several are a sequence of them.
        items = value if isinstance(value[0], (list, tuple)) else [value]
        return len(items), b"".join(
            struct.pack(">II", int(n), int(d)) for n, d in items
        )
    raise ValueError(f"unsupported EXIF type {typ}")


def _ifd_layout(entries: Sequence[Entry]) -> Tuple[List[Tuple[int, int, int, bytes]], int]:
    """Pre-encode entries and report how many bytes of overflow data they need.

    Returns the encoded entries plus the total size of the out-of-line data
    block, so IFD offsets can be resolved before anything is serialised.
    """
    encoded = []
    data_size = 0
    for tag, typ, value in entries:
        count, payload = _encode_value(typ, value)
        if len(payload) > 4:
            data_size += len(payload) + (len(payload) & 1)   # word-align
        encoded.append((tag, typ, count, payload))
    return encoded, data_size


def _ifd_size(entries: Sequence[Entry]) -> int:
    _, data_size = _ifd_layout(entries)
    return 2 + 12 * len(entries) + 4 + data_size


def _serialize_ifd(entries: Sequence[Entry], ifd_offset: int,
                   next_ifd: int = 0) -> bytes:
    """Serialise one IFD placed at ``ifd_offset`` from the TIFF header start."""
    encoded, _ = _ifd_layout(entries)
    table = struct.pack(">H", len(encoded))
    data = b""
    data_base = ifd_offset + 2 + 12 * len(encoded) + 4

    for tag, typ, count, payload in encoded:
        table += struct.pack(">HHI", tag, typ, count)
        if len(payload) <= 4:
            table += payload + b"\x00" * (4 - len(payload))
        else:
            table += struct.pack(">I", data_base + len(data))
            data += payload + (b"\x00" if len(payload) & 1 else b"")

    return table + struct.pack(">I", next_ifd) + data


def _deg_to_dms(value: float) -> List[Tuple[int, int]]:
    """Decimal degrees to the (deg, min, sec) rationals EXIF stores."""
    value = abs(value)
    deg = int(value)
    minutes_f = (value - deg) * 60.0
    minutes = int(minutes_f)
    seconds = (minutes_f - minutes) * 60.0
    # Seconds carry the precision, so they get a large denominator.
    return [(deg, 1), (minutes, 1), (int(round(seconds * 1000)), 1000)]


def build_exif(fields: Dict[str, object]) -> bytes:
    """Build a complete APP1 segment (marker, length, ``Exif\\0\\0``, TIFF block).

    ``fields`` is the flat description the caller assembles; every key is
    optional except the ones a camera always writes.
    """
    date_str = str(fields.get("datetime", ""))          # "YYYY:MM:DD HH:MM:SS"

    ifd0: List[Entry] = [
        (TAG_MAKE, ASCII, fields.get("make", "")),
        (TAG_MODEL, ASCII, fields.get("model", "")),
        (TAG_ORIENTATION, SHORT, 1),
        (TAG_X_RESOLUTION, RATIONAL, (72, 1)),
        (TAG_Y_RESOLUTION, RATIONAL, (72, 1)),
        (TAG_RESOLUTION_UNIT, SHORT, 2),
        (TAG_SOFTWARE, ASCII, fields.get("software", "")),
        (TAG_DATETIME, ASCII, date_str),
        (TAG_YCBCR_POSITIONING, SHORT, 1),
    ]

    exif_ifd: List[Entry] = [
        (TAG_EXPOSURE_TIME, RATIONAL, fields.get("exposure_time", (1, 120))),
        (TAG_F_NUMBER, RATIONAL, fields.get("f_number", (16, 10))),
        (TAG_EXPOSURE_PROGRAM, SHORT, 2),
        (TAG_ISO, SHORT, int(fields.get("iso", 50))),
        (TAG_EXIF_VERSION, UNDEFINED, b"0232"),
        (TAG_DATETIME_ORIGINAL, ASCII, date_str),
        (TAG_DATETIME_DIGITIZED, ASCII, date_str),
        (TAG_SHUTTER_SPEED, RATIONAL, fields.get("shutter_speed", (6907, 1000))),
        (TAG_APERTURE, RATIONAL, fields.get("aperture", (1356, 1000))),
        (TAG_EXPOSURE_BIAS, RATIONAL, (0, 1)),
        (TAG_METERING_MODE, SHORT, 5),
        (TAG_FLASH, SHORT, 16),
        (TAG_FOCAL_LENGTH, RATIONAL, fields.get("focal_length", (570, 100))),
        (TAG_SUBSEC_ORIGINAL, ASCII, str(fields.get("subsec", "00"))),
        (TAG_COLOR_SPACE, SHORT, 1),
        (TAG_PIXEL_X, LONG, int(fields.get("width", 0))),
        (TAG_PIXEL_Y, LONG, int(fields.get("height", 0))),
        (TAG_WHITE_BALANCE, SHORT, 0),
        (TAG_FOCAL_35MM, SHORT, int(fields.get("focal_35mm", 26))),
    ]
    if fields.get("lens_make"):
        exif_ifd.append((TAG_LENS_MAKE, ASCII, fields["lens_make"]))
    if fields.get("lens_model"):
        exif_ifd.append((TAG_LENS_MODEL, ASCII, fields["lens_model"]))

    gps_ifd: List[Entry] = []
    lat, lon = fields.get("lat"), fields.get("lon")
    if lat is not None and lon is not None:
        alt = float(fields.get("alt", 0.0))
        gps_ifd = [
            (TAG_GPS_VERSION, BYTE, b"\x02\x03\x00\x00"),
            (TAG_GPS_LAT_REF, ASCII, "N" if lat >= 0 else "S"),
            (TAG_GPS_LAT, RATIONAL, _deg_to_dms(float(lat))),
            (TAG_GPS_LON_REF, ASCII, "E" if lon >= 0 else "W"),
            (TAG_GPS_LON, RATIONAL, _deg_to_dms(float(lon))),
            (TAG_GPS_ALT_REF, BYTE, b"\x00" if alt >= 0 else b"\x01"),
            (TAG_GPS_ALT, RATIONAL, (int(round(abs(alt) * 100)), 100)),
        ]

    # Sub-IFD pointers live in IFD0, but their targets sit after it, so the
    # sizes have to be resolved first. Pointer values are inline LONGs, so
    # adding them cannot change any data-block size — one extra pass is enough.
    ifd0_with_pointers = list(ifd0) + [(TAG_EXIF_IFD, LONG, 0)]
    if gps_ifd:
        ifd0_with_pointers.append((TAG_GPS_IFD, LONG, 0))

    ifd0_size = _ifd_size(ifd0_with_pointers)
    exif_offset = 8 + ifd0_size
    gps_offset = exif_offset + _ifd_size(exif_ifd)

    final_ifd0 = list(ifd0) + [(TAG_EXIF_IFD, LONG, exif_offset)]
    if gps_ifd:
        final_ifd0.append((TAG_GPS_IFD, LONG, gps_offset))

    tiff = b"MM\x00\x2a" + struct.pack(">I", 8)
    tiff += _serialize_ifd(final_ifd0, 8, next_ifd=0)
    tiff += _serialize_ifd(exif_ifd, exif_offset)
    if gps_ifd:
        tiff += _serialize_ifd(gps_ifd, gps_offset)

    payload = b"Exif\x00\x00" + tiff
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("EXIF block too large for one APP1 segment")
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def insert_exif(jpeg: bytes, app1: bytes) -> bytes:
    """Replace whatever APP0/APP1 segments a JPEG carries with ``app1``.

    Phone cameras write an Exif APP1 and no JFIF APP0, so the JFIF block ffmpeg
    emits is dropped rather than kept alongside — leaving both is a tell.
    """
    if not jpeg.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG (no SOI marker)")

    pos = 2
    while pos + 4 <= len(jpeg) and jpeg[pos] == 0xFF and jpeg[pos + 1] in (0xE0, 0xE1):
        seg_len = struct.unpack(">H", jpeg[pos + 2:pos + 4])[0]
        if seg_len < 2:
            break
        pos += 2 + seg_len

    return b"\xff\xd8" + app1 + jpeg[pos:]


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    import zlib
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def insert_png_text(png: bytes, entries: Sequence[Tuple[str, str]]) -> bytes:
    """Insert ``tEXt`` chunks right after the PNG header.

    PNG has no EXIF convention that viewers agree on, so the camera identity
    goes in as plain text keys, which is what Android's PNG export does.
    """
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")

    # IHDR is fixed-length: 8-byte signature, then 4 len + 4 type + 13 + 4 crc.
    split = 8 + 25
    chunks = b"".join(
        _png_chunk(b"tEXt", key.encode("latin-1", "replace") + b"\x00"
                   + value.encode("latin-1", "replace"))
        for key, value in entries
    )
    return png[:split] + chunks + png[split:]


def read_png_text(path: str) -> Dict[str, str]:
    """Read back ``tEXt`` chunks as a key/value map. Verification helper only."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return {}

    found: Dict[str, str] = {}
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        if kind == b"IDAT" or kind == b"IEND":
            break
        if kind == b"tEXt":
            payload = data[pos + 8:pos + 8 + length]
            key, _, value = payload.partition(b"\x00")
            found[key.decode("latin-1", "replace")] = value.decode("latin-1", "replace")
        pos += 12 + length
    return found


def read_exif_strings(path: str) -> Optional[Dict[str, str]]:
    """Read back the ASCII fields of an APP1 block. Verification helper only."""
    with open(path, "rb") as fh:
        data = fh.read()
    idx = data.find(b"Exif\x00\x00", 0, 65536)
    if idx < 0:
        return None
    tiff = data[idx + 6:]
    if tiff[:2] != b"MM":
        return None

    found: Dict[str, str] = {}

    def walk(offset: int, depth: int = 0):
        if depth > 2 or offset + 2 > len(tiff):
            return
        count = struct.unpack(">H", tiff[offset:offset + 2])[0]
        for i in range(count):
            base = offset + 2 + 12 * i
            if base + 12 > len(tiff):
                return
            tag, typ, n = struct.unpack(">HHI", tiff[base:base + 8])
            raw = tiff[base + 8:base + 12]
            if typ == LONG and tag in (TAG_EXIF_IFD, TAG_GPS_IFD):
                walk(struct.unpack(">I", raw)[0], depth + 1)
            elif typ == ASCII:
                size = n * _TYPE_SIZE[ASCII]
                if size > 4:
                    start = struct.unpack(">I", raw)[0]
                    value = tiff[start:start + size]
                else:
                    value = raw[:size]
                found[f"0x{tag:04X}"] = value.rstrip(b"\x00").decode(
                    "ascii", "replace")

    walk(8)
    return found

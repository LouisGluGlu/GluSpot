"""Bounded QQ online-QRC decoding, with no optional crypto dependency.

The generic DES below is an independent implementation of the bit operations
and tables specified by NIST FIPS PUB 46-3 (1999), not imported provider code.
QQ's wire format uses little-endian 32-bit words, two nonstandard S-box
entries, a one-bit shift in the D key selection, a fixed protocol key and a
zlib stream with zero block padding.
Those protocol facts were checked against PlayLyricInfo responses and Folia
62c35156b01e165201dc999e0961f2e0e6dff68c; Folia's AGPL code is not included.
This only decodes lyrics returned by a lyric endpoint; it handles no audio.
"""
from __future__ import annotations

import re
import zlib
from functools import lru_cache


MAX_ENCRYPTED_BYTES = 128 * 1024
MAX_DECOMPRESSED_BYTES = 2 * 1024 * 1024
_QRC_KEY = b"!@#)(*$%123ZXC!@!@#)(NHL".ljust(24, b"\0")
_IP = tuple(int(x) for x in """
58 50 42 34 26 18 10 2 60 52 44 36 28 20 12 4
62 54 46 38 30 22 14 6 64 56 48 40 32 24 16 8
57 49 41 33 25 17 9 1 59 51 43 35 27 19 11 3
61 53 45 37 29 21 13 5 63 55 47 39 31 23 15 7
""".split())
_FP = tuple(int(x) for x in """
40 8 48 16 56 24 64 32 39 7 47 15 55 23 63 31
38 6 46 14 54 22 62 30 37 5 45 13 53 21 61 29
36 4 44 12 52 20 60 28 35 3 43 11 51 19 59 27
34 2 42 10 50 18 58 26 33 1 41 9 49 17 57 25
""".split())
_E = tuple(int(x) for x in """
32 1 2 3 4 5 4 5 6 7 8 9 8 9 10 11 12 13
12 13 14 15 16 17 16 17 18 19 20 21 20 21 22 23 24 25
24 25 26 27 28 29 28 29 30 31 32 1
""".split())
_P = tuple(int(x) for x in """
16 7 20 21 29 12 28 17 1 15 23 26 5 18 31 10
2 8 24 14 32 27 3 9 19 13 30 6 22 11 4 25
""".split())
_PC1 = tuple(int(x) for x in """
57 49 41 33 25 17 9 1 58 50 42 34 26 18 10 2
59 51 43 35 27 19 11 3 60 52 44 36
63 55 47 39 31 23 15 7 62 54 46 38 30 22 14 6
61 53 45 37 29 21 13 5 28 20 12 4
""".split())
_PC2 = tuple(int(x) for x in """
14 17 11 24 1 5 3 28 15 6 21 10 23 19 12 4 26 8 16 7 27 20 13 2
41 52 31 37 47 55 30 40 51 45 33 48 44 49 39 56 34 53 46 42 50 36 29 32
""".split())
_SHIFTS = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)
_SBOX = tuple(tuple(int(x) for x in box.split()) for box in (
    """14 4 13 1 2 15 11 8 3 10 6 12 5 9 0 7
    0 15 7 4 14 2 13 1 10 6 12 11 9 5 3 8
    4 1 14 8 13 6 2 11 15 12 9 7 3 10 5 0
    15 12 8 2 4 9 1 7 5 11 3 14 10 0 6 13""",
    """15 1 8 14 6 11 3 4 9 7 2 13 12 0 5 10
    3 13 4 7 15 2 8 14 12 0 1 10 6 9 11 5
    0 14 7 11 10 4 13 1 5 8 12 6 9 3 2 15
    13 8 10 1 3 15 4 2 11 6 7 12 0 5 14 9""",
    """10 0 9 14 6 3 15 5 1 13 12 7 11 4 2 8
    13 7 0 9 3 4 6 10 2 8 5 14 12 11 15 1
    13 6 4 9 8 15 3 0 11 1 2 12 5 10 14 7
    1 10 13 0 6 9 8 7 4 15 14 3 11 5 2 12""",
    """7 13 14 3 0 6 9 10 1 2 8 5 11 12 4 15
    13 8 11 5 6 15 0 3 4 7 2 12 1 10 14 9
    10 6 9 0 12 11 7 13 15 1 3 14 5 2 8 4
    3 15 0 6 10 1 13 8 9 4 5 11 12 7 2 14""",
    """2 12 4 1 7 10 11 6 8 5 3 15 13 0 14 9
    14 11 2 12 4 7 13 1 5 0 15 10 3 9 8 6
    4 2 1 11 10 13 7 8 15 9 12 5 6 3 0 14
    11 8 12 7 1 14 2 13 6 15 0 9 10 4 5 3""",
    """12 1 10 15 9 2 6 8 0 13 3 4 14 7 5 11
    10 15 4 2 7 12 9 5 6 1 13 14 0 11 3 8
    9 14 15 5 2 8 12 3 7 0 4 10 1 13 11 6
    4 3 2 12 9 5 15 10 11 14 1 7 6 0 8 13""",
    """4 11 2 14 15 0 8 13 3 12 9 7 5 10 6 1
    13 0 11 7 4 9 1 10 14 3 5 12 2 15 8 6
    1 4 11 13 12 3 7 14 10 15 6 8 0 5 9 2
    6 11 13 8 1 4 10 7 9 5 0 15 14 2 3 12""",
    """13 2 8 4 6 15 11 1 10 9 3 14 5 0 12 7
    1 15 13 8 10 3 7 4 12 5 6 11 0 14 9 2
    7 11 4 1 9 12 14 2 0 6 10 13 15 3 5 8
    2 1 14 7 4 10 8 13 15 12 9 0 3 5 6 11""",
))


def _permute(value: int, input_bits: int, positions: tuple[int, ...]) -> int:
    result = 0
    for position in positions:
        result = result << 1 | (value >> (input_bits - position) & 1)
    return result


def _schedule(key: bytes, *, qq_variant: bool = False) -> tuple[int, ...]:
    selected = _permute(int.from_bytes(key, "big"), 64, _PC1)
    left, right = selected >> 28, selected & 0xfffffff
    result = []
    for shift in _SHIFTS:
        left = (left << shift | left >> (28 - shift)) & 0xfffffff
        right = (right << shift | right >> (28 - shift)) & 0xfffffff
        selected_right = right << 1 & 0xfffffff if qq_variant else right
        result.append(_permute(left << 28 | selected_right, 56, _PC2))
    return tuple(result)


def _des_block(block: bytes, schedule: tuple[int, ...], *, qq_variant: bool = False) -> bytes:
    state = _permute(int.from_bytes(block, "big"), 64, _IP)
    left, right = state >> 32, state & 0xffffffff
    for key in schedule:
        expanded = _permute(right, 32, _E) ^ key
        substituted = 0
        for box_number, box in enumerate(_SBOX):
            six = expanded >> (42 - 6 * box_number) & 63
            row = (six >> 4 & 2) | (six & 1)
            column = six >> 1 & 15
            index = row * 16 + column
            # QQ's protocol changes S2[1,7] and S4[3,5].
            value = (15 if box_number == 1 else 10) if qq_variant and (
                box_number == 1 and index == 23 or box_number == 3 and index == 53
            ) else box[index]
            substituted = substituted << 4 | value
        left, right = right, left ^ _permute(substituted, 32, _P)
    return _permute(right << 32 | left, 64, _FP).to_bytes(8, "big")


def _word_order(block: bytes) -> bytes:
    return block[:4][::-1] + block[4:8][::-1]


@lru_cache(maxsize=1)
def _qrc_schedules() -> tuple[tuple[int, ...], ...]:
    first, second, third = (_schedule(_word_order(_QRC_KEY[i:i + 8]), qq_variant=True) for i in (0, 8, 16))
    return tuple(reversed(third)), second, tuple(reversed(first))


def decrypt_qrc(value: str) -> str:
    """Decode one online-QRC hex field; reject truncation and inflated bombs.

    Empty/invalid responses raise ValueError so a caller can keep its usable
    LRC. Trailing zero bytes are protocol block padding, not a truncated zlib
    stream to be accepted. A malformed field never becomes plain lyrics.
    """
    if not isinstance(value, str):
        raise ValueError("QRC field is not text")
    value = value.strip()
    if not value or len(value) > 2 * MAX_ENCRYPTED_BYTES or len(value) % 16 or not re.fullmatch(r"[0-9a-fA-F]+", value):
        raise ValueError("Invalid QRC hex or block length")
    encrypted = bytes.fromhex(value)
    decoded = bytearray()
    schedules = _qrc_schedules()
    for offset in range(0, len(encrypted), 8):
        block = _word_order(encrypted[offset:offset + 8])
        for schedule in schedules:
            block = _des_block(block, schedule, qq_variant=True)
        decoded.extend(_word_order(block))
    try:
        stream = zlib.decompressobj()
        raw = stream.decompress(decoded, MAX_DECOMPRESSED_BYTES + 1)
        if len(raw) > MAX_DECOMPRESSED_BYTES or not stream.eof or stream.unconsumed_tail:
            raise ValueError("Truncated or oversized QRC stream")
        if any(stream.unused_data):
            raise ValueError("Invalid QRC padding")
        return raw.decode("utf-8-sig")
    except (UnicodeError, zlib.error) as error:
        raise ValueError("Invalid QRC compressed text") from error

# CHOSEN-TARGET: crcmod
"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the

"""
import struct
import crcmod

CRC_LEN = 4

# Standard CRC-32 (ISO-HDLC / zlib-compatible) algorithm, defined explicitly
# so the numeric result matches zlib.crc32 exactly.
_crc32_func = crcmod.mkCrcFun(
    poly=0x104C11DB7,
    initCrc=0xFFFFFFFF,
    rev=True,
    xorOut=0xFFFFFFFF,
)


def crc32(payload: bytes) -> int:
    """CRC-32 of *payload* as an unsigned 32-bit integer."""
    return _crc32_func(payload) & 0xFFFFFFFF


def build_frame(payload: bytes) -> bytes:
    """Return *payload* with a big-endian CRC-32 trailer appended."""
    return payload + struct.pack(">I", crc32(payload))


def verify_frame(frame: bytes) -> bool:
    """True iff *frame*'s trailing CRC-32 matches its payload."""
    if len(frame) < CRC_LEN:
        return False
    payload, trailer = frame[:-CRC_LEN], frame[-CRC_LEN:]
    return crc32(payload) == struct.unpack(">I", trailer)[0]


def extract_payload(frame: bytes) -> bytes:
    """Return the payload of a valid frame, or raise ValueError on CRC mismatch."""
    if not verify_frame(frame):
        raise ValueError("CRC check failed")
    return frame[:-CRC_LEN]

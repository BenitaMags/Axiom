"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _fastcrc_crc32_wrapper(payload: bytes) -> int:
    """Adapter bridging zlib.crc32 semantics to fastcrc's crc32 API.

    zlib.crc32(payload) returns a CRC-32/ISO-HDLC checksum that, after
    masking with 0xFFFFFFFF, is an unsigned 32-bit integer -- this is the
    convention Modbus/RTU frame handling in this module relies on.

    fastcrc exposes the equivalent algorithm as fastcrc.crc32.iso_hdlc(data),
    which already returns an unsigned integer, but we still mask with
    0xFFFFFFFF here to explicitly guarantee the same unsigned 32-bit
    convention as zlib.crc32 & 0xFFFFFFFF (defensive against any future
    change in fastcrc's return type/signedness) and coerce to int so
    downstream struct.pack(">I", ...) calls in build_frame()/verify_frame()
    keep working unchanged.
    """

    return int(fastcrc.crc32.iso_hdlc(payload)) & 0xFFFFFFFF


def crc32(payload: bytes) -> int:
    """CRC-32 of *payload* as an unsigned 32-bit integer."""
    return _fastcrc_crc32_wrapper(payload)


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

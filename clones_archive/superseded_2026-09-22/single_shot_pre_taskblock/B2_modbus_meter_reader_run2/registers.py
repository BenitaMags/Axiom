"""registers -- register map and word decoding for a Modbus power meter.

Decodes big-endian 16-bit register words into engineering values. Kept separate from
the transport so that decoding is reusable and independently testable.
"""
import struct

# name -> (start address, word count, encoding, scale)
REGISTERS = {
    "voltage": (0x0000, 2, "f32", 1.0),
    "current": (0x0002, 2, "f32", 1.0),
    "power":   (0x0004, 2, "f32", 1.0),
    "energy":  (0x0006, 2, "u32", 0.001),  # Wh -> kWh
}


def decode(encoding: str, words, scale: float) -> float:
    """Decode a sequence of 16-bit words into a scaled engineering value."""
    raw = b"".join(struct.pack(">H", w & 0xFFFF) for w in words)
    if encoding == "f32":
        value = struct.unpack(">f", raw)[0]
    elif encoding == "u32":
        value = struct.unpack(">I", raw)[0]
    else:
        raise ValueError(f"unknown encoding: {encoding}")
    return value * scale

"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Bridge attempt for pymodbus's ModbusTcpClient(host, port=...).

    pymodbus's ModbusTcpClient opens a Modbus **TCP** connection to a server
    listening at `host:port` over a network socket, and returns a client
    object exposing methods like `.read_holding_registers(...)` /
    `.connect()`.

    minimalmodbus has no TCP transport at all: its `minimalmodbus.Instrument`
    class is serial-only (RTU/ASCII) and is constructed from a local serial
    *port device path* (e.g. "/dev/ttyUSB0" or "COM3") plus a slave address,
    not a network host/port pair. There is therefore no minimalmodbus call
    that is actually equivalent to `ModbusTcpClient(host, port=port)`: the
    closest structural mimicry -- passing `host` in as if it were a serial
    device string -- would silently connect to the wrong transport/device
    and produce a client that looks valid but talks to nothing resembling
    the requested TCP endpoint.

    Rather than fabricate that incorrect, silently-wrong behavior, this
    wrapper documents the incompatibility and raises immediately so the gap
    is obvious at the call site instead of manifesting as a mysterious
    runtime failure deeper in the program.
    """
    raise NotImplementedError(
        "Cannot bridge pymodbus.ModbusTcpClient(host, port=...) to "
        "minimalmodbus: minimalmodbus only supports serial (RTU/ASCII) "
        "connections via a local serial port device path, not TCP "
        f"host/port pairs. Requested TCP target was {host!r}:{port!r}."
    )


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record

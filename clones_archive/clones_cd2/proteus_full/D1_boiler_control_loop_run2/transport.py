"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge wrapper for constructing a Modbus client.

    pymodbus's ``ModbusTcpClient(host, port=port)`` returns a TCP client object
    whose ``connect()`` method opens a network socket to a remote Modbus TCP
    server identified by ``host``/``port``. minimalmodbus, by contrast, only
    speaks Modbus RTU/ASCII over a local serial port: its ``Instrument`` class
    is constructed as ``Instrument(portname, slaveaddress)`` and manages the
    serial connection internally (there is no separate ``connect()`` step).

    There is no way to truly bridge TCP transport semantics into minimalmodbus
    without an external serial-to-TCP gateway device/library, which is outside
    minimalmodbus's own API. As a best-effort adapter using only
    minimalmodbus's public API and the standard library, this wrapper:

      * treats ``host`` as the local serial port identifier (e.g. "/dev/ttyUSB0"
        or "COM3") that minimalmodbus expects as its first constructor argument,
      * treats ``port`` as the Modbus slave address (minimalmodbus's second
        constructor argument), since minimalmodbus has no notion of a network
        port number, and
      * attaches a no-op ``connect()`` method to the returned instrument so
        that call sites written against pymodbus's ``client.connect()`` idiom
        continue to work unmodified, since minimalmodbus opens/manages the
        serial connection transparently on each read/write call.
    """
    import minimalmodbus  # lazy import for testability

    instrument = minimalmodbus.Instrument(host, port)
    instrument.connect = lambda: None
    return instrument


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability; unused now that
    # construction is routed through _axiom_modbus_client_wrapper below, kept in place per the
    # import-lock policy so the deterministic source->target import rewrite can still find it.
    del ModbusTcpClient

    client = _axiom_modbus_client_wrapper(host, port=port)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts

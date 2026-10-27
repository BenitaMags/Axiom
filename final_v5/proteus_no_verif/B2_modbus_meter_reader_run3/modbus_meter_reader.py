"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient/read_holding_registers API onto modbus-tk.

    pymodbus's ``ModbusTcpClient`` exposes ``.connect()`` and
    ``read_holding_registers(address, count, unit=...)``, the latter returning
    a response object with ``.registers`` (the decoded values) and
    ``.isError()`` (True on failure). modbus-tk's ``TcpMaster`` instead
    exposes ``.open()`` for connecting and ``.execute(slave_id, function_code,
    address, count)``, which returns a plain tuple of register values on
    success and raises a ``modbus_tk.modbus.ModbusError`` (or a socket/OSError)
    on failure -- it never returns a response object.

    This wrapper constructs and opens a ``TcpMaster`` and returns a small
    adapter object whose ``read_holding_registers`` method calls ``execute``
    with the READ_HOLDING_REGISTERS function code and repackages the result
    (or the failure) into an object exposing ``.registers`` and ``.isError()``,
    so existing call sites written against pymodbus's response-object
    contract keep working unchanged. No exception is swallowed silently: a
    failed read is reported via ``isError()`` returning True, mirroring how
    pymodbus itself reports a failed read as an error response rather than
    raising.
    """
    import modbus_tk.defines as cst
    import modbus_tk.modbus as modbus
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()

    class _Result:
        def __init__(self, registers, error=False):
            self.registers = registers
            self._error = error

        def isError(self):
            _spt_r1 = self._error
            return _spt_r1

    class _ClientAdapter:
        def __init__(self, master):
            self._master = master

        def read_holding_registers(self, address, count, unit=1):
            try:
                registers = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except (modbus.ModbusError, OSError):
                _spt_r2 = _Result((), error=True)
                return _spt_r2
            _spt_r3 = _Result(registers, error=False)
            return _spt_r3

        def connect(self):
            # Connection is already established in the constructor above;
            # kept so callers using pymodbus's connect()-then-read pattern
            # keep working unchanged.
            return True

        def close(self):
            self._master.close()
    _spt_r4 = _ClientAdapter(master)

    return _spt_r4


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if not (client is not None):
            self.client = _default_client(host)
        else:
            self.client = client
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

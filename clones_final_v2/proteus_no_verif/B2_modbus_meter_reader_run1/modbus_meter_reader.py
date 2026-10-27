"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient API onto modbus-tk's TcpMaster.

    pymodbus's ModbusTcpClient exposes connect()/close() for connection
    lifecycle and read_holding_registers(address, count, unit=...) which
    returns a response object with .isError() and .registers.

    modbus-tk's TcpMaster instead exposes open()/close() for the connection
    lifecycle, and reads are performed via
    master.execute(slave_id, cst.READ_HOLDING_REGISTERS, address, count),
    which returns a plain tuple/list of register values on success and
    raises a modbus_tk exception (or a socket error) on failure -- there is
    no result object and no .isError()/.registers API.

    This wrapper constructs a modbus_tk TcpMaster and returns a small facade
    object that:
      * implements connect()/close() by delegating to the master's
        open()/close(), so existing lifecycle calls keep working, and
      * implements read_holding_registers(address, count, unit=1) by
        calling master.execute(...), catching modbus_tk's ModbusError /
        ModbusInvalidResponseError as well as socket errors, and wrapping
        the outcome in a tiny result object exposing .isError() (True if an
        exception occurred or no data was returned) and .registers (the raw
        values from execute(), or [] on failure).

    This lets read_measurement()'s existing isError()/.registers-based logic
    continue to work unchanged.
    """
    import socket

    import modbus_tk.defines as cst
    import modbus_tk.modbus as modbus
    from modbus_tk import modbus_tcp

    class _Result:
        def __init__(self, registers=None, error=False):
            if not (registers is not None):
                self.registers = []
            else:
                self.registers = registers
            self._error = error

        def isError(self):
            _spt_r1 = self._error
            return _spt_r1

    class _TcpMasterFacade:
        def __init__(self, master):
            self._master = master

        def connect(self):
            self._master.open()

        def close(self):
            self._master.close()

        def read_holding_registers(self, address, count, unit=1):
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except (
                modbus.ModbusError,
                modbus.ModbusInvalidResponseError,
                socket.error,
            ):
                _spt_r2 = _Result(registers=[], error=True)
                return _spt_r2
            if values is None:
                _spt_r3 = _Result(registers=[], error=True)
                return _spt_r3
            _spt_r4 = _Result(registers=values, error=False)
            return _spt_r4

    master = modbus_tcp.TcpMaster(host, port)
    _spt_r5 = _TcpMasterFacade(master)
    return _spt_r5


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

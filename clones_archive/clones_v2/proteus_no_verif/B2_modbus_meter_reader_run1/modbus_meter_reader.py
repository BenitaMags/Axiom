"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode
import modbus_tk.modbus_tcp as modbus_tcp
import modbus_tk.defines as cst


class _ModbusTkResult:
    """Lightweight result facade mimicking pymodbus's response object.

    modbus_tk's ``master.execute(...)`` returns a plain tuple of register
    values on success and raises a ``modbus_tk`` exception on failure,
    whereas pymodbus returns a response object exposing ``.registers`` and
    ``.isError()``. This class wraps the tuple (or the error flag) so
    existing call sites written against the pymodbus contract keep working
    unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


def _modbus_tk_client_wrapper(host: str, port: int = 502):
    """Build a modbus_tk-backed facade exposing a pymodbus-like interface.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()``, ``close()`` and
    ``read_holding_registers(address, count, unit=...)``, the latter
    returning a response object with ``.registers``/``.isError()``.
    modbus_tk instead uses a ``TcpMaster`` with an
    ``execute(slave_id, function_code, address, count)`` call that returns a
    raw tuple and raises exceptions on error. This wrapper constructs a
    ``TcpMaster``, opens the connection, and returns a facade object that
    translates ``read_holding_registers`` calls into ``execute(...)`` calls,
    catching modbus_tk exceptions and reporting them via ``.isError()``
    instead of letting them propagate, so callers written against the
    pymodbus API do not need to change.
    """
    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()

    class _Facade:
        def __init__(self, master):
            self._master = master

        def connect(self):
            self._master.open()
            return True

        def close(self):
            self._master.close()

        def read_holding_registers(self, address, count, unit=1):
            try:
                registers = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except Exception:
                return _ModbusTkResult(registers=None, error=True)
            return _ModbusTkResult(registers=registers, error=False)

    return _Facade(master)


def _default_client(host: str, port: int = 502):

    client = _modbus_tk_client_wrapper(host, port=port)
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

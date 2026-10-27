"""Adapter that bridges modbus-tk's Master API to a pymodbus-shaped client
interface, used by :mod:`transport`.

Kept as a separate module so the modbus-tk dependency stays isolated behind
a single factory function that :mod:`transport` imports and calls lazily
from within its own factory functions (the actual ``import modbus_tk``
statements remain deferred inside :func:`build_tcp_client`, so importing
this module does not require modbus-tk to be installed).
"""


class _ExecutionResult:
    """Mimics the pymodbus response object contract: ``.isError()`` plus,
    for reads, a ``.registers`` list.
    """

    def __init__(self, registers=None, error=None):
        self._error = error
        self.registers = list(registers) if registers is not None else []

    def isError(self) -> bool:
        return self._error is not None


class TcpMasterAdapter:
    """Wraps a modbus-tk ``TcpMaster`` so it can be used wherever a
    pymodbus-style client (``connect``/``read_holding_registers``/
    ``write_register``) is expected.
    """

    def __init__(self, host, port, master_factory, function_codes, errors):
        self._errors = errors
        self._cst = function_codes
        self._master = master_factory(host=host, port=port)
        self._master.set_timeout(5.0)
        self._master.open()

    def connect(self):
        # modbus-tk opens its transport during construction (via .open()
        # above), so there is nothing left to do here; kept for callers
        # that expect a pymodbus-style explicit connect() step.
        return True

    def read_holding_registers(self, address, count, unit=1):
        try:
            result = self._master.execute(
                unit, self._cst.READ_HOLDING_REGISTERS, address, count
            )
        except self._errors as exc:
            return _ExecutionResult(error=exc)
        return _ExecutionResult(registers=result)

    def write_register(self, address, value, unit=1):
        try:
            self._master.execute(
                unit, self._cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
        except self._errors as exc:
            return _ExecutionResult(error=exc)
        return _ExecutionResult()


def build_tcp_client(host: str, port: int = 502):
    """Construct a :class:`TcpMasterAdapter` backed by modbus-tk's
    ``TcpMaster``, exposing the pymodbus-shaped interface :class:`ModbusIO`
    relies on.
    """
    import socket

    import modbus_tk.defines as cst
    import modbus_tk.modbus as modbus
    from modbus_tk.modbus_tcp import TcpMaster

    errors = (modbus.ModbusError, socket.error)
    return TcpMasterAdapter(host, port, TcpMaster, cst, errors)

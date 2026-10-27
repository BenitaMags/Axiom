"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbustcp_client_wrapper(host, port=502):
    """Bridge pymodbus-style TCP client construction to modbus-tk.

    pymodbus's ``ModbusTcpClient(host, port=port)`` returns an object with
    ``connect()``/``close()`` methods that lazily open/close the socket.
    modbus-tk instead exposes a ``modbus_tcp.TcpMaster(host, port)`` object
    whose connection is opened via an explicit ``open()`` call and used
    directly with its ``execute()`` API -- it has no ``connect()``/``close()``
    pair with pymodbus's exact names/semantics.

    This wrapper builds the modbus-tk ``TcpMaster``, opens the underlying
    connection immediately, and returns a small adapter object exposing
    ``connect()`` (a no-op, since the connection is already open) and
    ``close()`` (delegates to the master's ``close()``), so call sites written
    against pymodbus's connect/close lifecycle keep working while the actual
    I/O happens through modbus-tk. The adapter also exposes ``.master`` so
    other wrappers (e.g. the register-read wrapper) can reach the real
    modbus-tk master instance.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host, port)
    master.open()

    class _AxiomTcpClientAdapter:
        def __init__(self, master):
            self.master = master

        def connect(self):
            # modbus-tk's TcpMaster is already open by the time this adapter
            # is constructed, so satisfy pymodbus-style call sites with a
            # harmless no-op.
            return True

        def close(self):
            self.master.close()

    return _AxiomTcpClientAdapter(master)


def _axiom_read_holding_registers_wrapper(master, unit, address, count):
    """Bridge pymodbus-style holding-register reads to modbus-tk.

    pymodbus's ``read_holding_registers(address, count, unit=...)`` returns a
    response object with a boolean ``.isError()`` method and a ``.registers``
    list on success. modbus-tk instead uses
    ``master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)``,
    which returns a raw tuple/list of register values on success and raises a
    modbus-tk exception on failure (no error-flag object at all).

    This wrapper calls modbus-tk's ``execute()``, catching any exception it
    raises and translating both outcomes into a small object exposing
    ``.registers`` (list) and ``.isError()`` (bool), matching the shape that
    existing pymodbus-based call sites expect.
    """
    import modbus_tk.defines as cst

    class _AxiomReadResult:
        def __init__(self, registers=None, error=False):
            self.registers = list(registers) if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    try:
        registers = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
        return _AxiomReadResult(registers, error=False)
    except Exception:
        return _AxiomReadResult(error=True)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbustcp_client_wrapper(host, port=port)
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
            result = _axiom_read_holding_registers_wrapper(
                self.client.master, self.unit, address, count
            )
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record

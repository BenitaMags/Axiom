"""modbus_adapter -- glue between pymodbus-shaped call sites and modbus-tk.

Extracted from transport.py: builds fake pymodbus-style response objects
around modbus-tk's execute()/ModbusError API, and provides the TCP client
adapter used by transport.ModbusIO.
"""


class _FakeReadResponse:
    def __init__(self, registers=None, errored=False):
        self.registers = list(registers) if registers is not None else []
        self._errored = errored

    def isError(self):
        return self._errored


class _FakeWriteResponse:
    def __init__(self, errored=False):
        self._errored = errored

    def isError(self):
        return self._errored


def read_holding_registers_wrapper(master, unit, address, count):
    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mt_exceptions

    try:
        result = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    except mt_exceptions.ModbusError:
        return _FakeReadResponse(errored=True)
    return _FakeReadResponse(registers=result)


def write_register_wrapper(master, unit, address, value):
    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mt_exceptions

    try:
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value)
    except mt_exceptions.ModbusError:
        return _FakeWriteResponse(errored=True)
    return _FakeWriteResponse(errored=False)


class ModbusTcpAdapter:
    def __init__(self, host, port):
        import modbus_tk.modbus_tcp as modbus_tcp
        self._master = modbus_tcp.TcpMaster(host, port)

    def connect(self):
        self._master.open()

    def read_holding_registers(self, address, count, unit=1):
        return read_holding_registers_wrapper(self._master, unit, address, count)

    def write_register(self, address, value, unit=1):
        return write_register_wrapper(self._master, unit, address, value)

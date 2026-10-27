# CHOSEN-TARGET: umodbus
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.
"""

import socket
import struct


class _ModbusError(Exception):
    """Raised internally when a Modbus exception response is received."""


class _UmodbusTcpClientAdapter:
    """Adapter exposing the small subset of the old pymodbus client API
    (``read_holding_registers`` / ``write_register`` returning objects with
    ``.isError()``/``.registers``) on top of ``umodbus``'s TCP functions.
    """

    def __init__(self, host: str, port: int = 502):
        from umodbus.client import tcp as _umodbus_tcp

        self._umodbus_tcp = _umodbus_tcp
        self.host = host
        self.port = port
        self.sock = socket.create_connection((host, port))

    def connect(self):
        return True

    class _Result:
        def __init__(self, registers=None, error=None):
            self.registers = registers
            self._error = error

        def isError(self):
            return self._error is not None

    def read_holding_registers(self, address, count, unit=1):
        try:
            message = self._umodbus_tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            response = self._umodbus_tcp.send_message(message, self.sock)
            if isinstance(response, (list, tuple)):
                registers = list(response)
            else:
                registers = [response]
            return self._Result(registers=registers)
        except Exception as exc:  # noqa: BLE001 - normalize any backend error
            return self._Result(error=str(exc))

    def write_register(self, address, value, unit=1):
        try:
            message = self._umodbus_tcp.write_single_register(
                slave_id=unit, address=address, value=value
            )
            self._umodbus_tcp.send_message(message, self.sock)
            return self._Result(registers=[value])
        except Exception as exc:  # noqa: BLE001 - normalize any backend error
            return self._Result(error=str(exc))


def _default_client(host: str, port: int = 502):
    client = _UmodbusTcpClientAdapter(host, port=port)
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

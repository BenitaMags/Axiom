# CHOSEN-TARGET: umodbus
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.
"""

import socket
import struct


def _default_client(host: str, port: int = 502):
    from umodbus.client import tcp  # lazy import for testability

    sock = socket.create_connection((host, port))
    return _UModbusTcpClient(sock, tcp)


class _UModbusResponse:
    """Adapter result object mimicking the pymodbus-style read/write result."""

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _UModbusTcpClient:
    """Thin adapter around umodbus.client.tcp to expose a read/write register API
    compatible with what ModbusIO expects."""

    def __init__(self, sock, tcp_module):
        self.sock = sock
        self.tcp = tcp_module

    def read_holding_registers(self, address, count, unit=1):
        try:
            message = self.tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            response = self.tcp.send_message(message, self.sock)
            if isinstance(response, (list, tuple)):
                registers = list(response)
            else:
                registers = [response]
            return _UModbusResponse(registers=registers, error=False)
        except Exception:
            return _UModbusResponse(error=True)

    def write_register(self, address, value, unit=1):
        try:
            message = self.tcp.write_single_register(
                slave_id=unit, address=address, value=value
            )
            self.tcp.send_message(message, self.sock)
            return _UModbusResponse(registers=[value], error=False)
        except Exception:
            return _UModbusResponse(error=True)


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

"""transport -- Modbus field I/O for the boiler supervisor.

Uses umodbus for TCP client communication (read/write API and error model
required glue code since umodbus's client API differs from pymodbus's).
The client is dependency-injected, so tests can supply a fake client
implementing `read_holding_registers` / `write_register` returning objects
with `.isError()` and `.registers`, matching the original transport's
expected interface for backward compatibility with existing test doubles.
"""

import socket


class _ErrorResult:
    def __init__(self, message):
        self._message = message
        self.registers = []

    def isError(self):
        return True


class _OkResult:
    def __init__(self, registers=None):
        self.registers = registers or []

    def isError(self):
        return False


def _default_client(host: str, port: int = 502):
    from umodbus.client import tcp  # lazy import for testability

    sock = socket.create_connection((host, port))

    class _UmodbusClientAdapter:
        """Adapts umodbus's functional TCP client API to the read/write
        object-based interface used by ModbusIO, matching the shape the
        rest of this module expects (result objects with .isError()/.registers)."""

        def __init__(self, sock):
            self._sock = sock

        def read_holding_registers(self, address, count, unit=1):
            try:
                message = tcp.read_holding_registers(
                    slave_id=unit, starting_address=address, quantity=count
                )
                response = tcp.send_message(message, self._sock)
                registers = response if isinstance(response, list) else [response]
                return _OkResult(registers)
            except Exception as exc:  # noqa: BLE001 - map any client error uniformly
                return _ErrorResult(str(exc))

        def write_register(self, address, value, unit=1):
            try:
                message = tcp.write_single_register(
                    slave_id=unit, address=address, value=value
                )
                tcp.send_message(message, self._sock)
                return _OkResult()
            except Exception as exc:  # noqa: BLE001
                return _ErrorResult(str(exc))

    return _UmodbusClientAdapter(sock)


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

"""transport -- Modbus field I/O for the boiler supervisor.

Uses `umodbus` instead of `pymodbus` for the underlying Modbus TCP transport.
umodbus's client API is function-based (it builds a raw ADU/PDU and expects
the caller to supply a `send_message(adu, sock)` callable) rather than the
object-oriented pymodbus client, so this module wraps it in a small adapter
that exposes the same read/write shape the rest of the program relies on
(read/write API and error model -> glue required). The client is
dependency-injected.
"""
import socket


def _default_client(host: str, port: int = 502):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    return _UmodbusSocketClient(sock)


class _UmodbusSocketClient:
    """Adapter around a raw TCP socket using umodbus's client helpers,
    exposing a pymodbus-like `read_holding_registers` / `write_register` API.
    """

    def __init__(self, sock):
        self.sock = sock

    def read_holding_registers(self, address, count, unit=1):
        from umodbus.client import tcp

        try:
            message = tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            response = tcp.send_message(message, self.sock)
            registers = response if isinstance(response, list) else [response]
            return _Result(registers=registers)
        except Exception as exc:
            return _Result(error=exc)

    def write_register(self, address, value, unit=1):
        from umodbus.client import tcp

        try:
            message = tcp.write_single_register(
                slave_id=unit, address=address, value=value
            )
            tcp.send_message(message, self.sock)
            return _Result(registers=[value])
        except Exception as exc:
            return _Result(error=exc)


class _Result:
    def __init__(self, registers=None, error=None):
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: int) -> int:
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

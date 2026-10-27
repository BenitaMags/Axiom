# CHOSEN-TARGET: umodbus
"""transport -- Modbus field I/O for the boiler supervisor.

Rewritten to depend on `umodbus` instead of `pymodbus`. `umodbus` only ships
low-level TCP/serial framing helpers and does not provide a batteries-included
client object, so this module wraps a plain `socket` connection with the
`umodbus.client` request builders/parsers to provide the same read/write API
that `ModbusIO` exposed before (read/write API and error model -> glue
required). The client is dependency-injected, same as before.
"""
import socket


def _default_client(host: str, port: int = 502):
    sock = socket.create_connection((host, port))
    return sock


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        from umodbus.client import tcp

        try:
            request = tcp.read_holding_registers(
                slave_id=self.unit, starting_address=address, quantity=1
            )
            response = tcp.send_message(request, self.client)
        except Exception as exc:
            raise IOError(f"Modbus read failed @ {address:#06x}") from exc
        return response[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        from umodbus.client import tcp

        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        try:
            request = tcp.write_single_register(
                slave_id=self.unit, address=address, value=counts
            )
            tcp.send_message(request, self.client)
        except Exception as exc:
            raise IOError(f"Modbus write failed @ {address:#06x}") from exc
        return counts

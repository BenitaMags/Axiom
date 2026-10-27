# CHOSEN-TARGET: first-party
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.

This module no longer depends on `pymodbus`. Instead it implements a minimal,
self-contained Modbus-TCP client sufficient for the read-holding-registers
(function code 0x03) and write-single-register (function code 0x06)
operations used by `ModbusIO`. The public API (`ModbusIO` and its methods)
is unchanged, and the default client's request/response objects mimic the
subset of the pymodbus response API used here (`.isError()`, `.registers`).
"""
import socket
import struct


class ModbusResponse:
    """Minimal stand-in for a pymodbus response object."""

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _SimpleModbusTcpClient:
    """A minimal Modbus-TCP client implementing only what ModbusIO needs.

    Supports:
      - read_holding_registers(address, count, unit=...)
      - write_register(address, value, unit=...)

    Both methods return objects exposing `.isError()` and (for reads)
    `.registers`, matching the subset of the pymodbus API relied upon here.
    """

    def __init__(self, host: str, port: int = 502, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self._transaction_id = 0

    def connect(self) -> bool:
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            return True
        except OSError:
            self.sock = None
            return False

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) % 0x10000
        return self._transaction_id

    def _send_recv(self, unit_id: int, pdu: bytes) -> bytes:
        if self.sock is None:
            raise IOError("Modbus client is not connected")

        tid = self._next_transaction_id()
        protocol_id = 0
        length = len(pdu) + 1  # + unit id byte
        header = struct.pack(">HHHB", tid, protocol_id, length, unit_id)
        frame = header + pdu

        try:
            self.sock.sendall(frame)
            resp_header = self._recv_exact(7)
            (resp_tid, resp_proto, resp_len, resp_unit) = struct.unpack(">HHHB", resp_header)
            remaining = resp_len - 1
            resp_pdu = self._recv_exact(remaining)
        except OSError as exc:
            raise IOError(f"Modbus transport error: {exc}") from exc

        return resp_pdu

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise IOError("Modbus connection closed unexpectedly")
            data += chunk
        return data

    def read_holding_registers(self, address: int, count: int, unit: int = 1) -> ModbusResponse:
        function_code = 0x03
        pdu = struct.pack(">BHH", function_code, address, count)
        try:
            resp_pdu = self._send_recv(unit, pdu)
        except IOError:
            return ModbusResponse(error=True)

        if len(resp_pdu) == 0:
            return ModbusResponse(error=True)

        resp_function = resp_pdu[0]
        if resp_function & 0x80:
            # Exception response
            return ModbusResponse(error=True)

        if resp_function != function_code:
            return ModbusResponse(error=True)

        byte_count = resp_pdu[1]
        reg_bytes = resp_pdu[2:2 + byte_count]
        if len(reg_bytes) != byte_count or byte_count != count * 2:
            return ModbusResponse(error=True)

        registers = list(struct.unpack(f">{count}H", reg_bytes))
        return ModbusResponse(registers=registers, error=False)

    def write_register(self, address: int, value: int, unit: int = 1) -> ModbusResponse:
        function_code = 0x06
        pdu = struct.pack(">BHH", function_code, address, value)
        try:
            resp_pdu = self._send_recv(unit, pdu)
        except IOError:
            return ModbusResponse(error=True)

        if len(resp_pdu) == 0:
            return ModbusResponse(error=True)

        resp_function = resp_pdu[0]
        if resp_function & 0x80:
            return ModbusResponse(error=True)

        if resp_function != function_code:
            return ModbusResponse(error=True)

        if len(resp_pdu) < 5:
            return ModbusResponse(error=True)

        resp_address, resp_value = struct.unpack(">HH", resp_pdu[1:5])
        if resp_address != address or resp_value != value:
            return ModbusResponse(error=True)

        return ModbusResponse(error=False)


def _default_client(host: str, port: int = 502):
    client = _SimpleModbusTcpClient(host, port=port)
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

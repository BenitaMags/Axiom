# CHOSEN-TARGET: first-party
"""transport -- Modbus field I/O for the boiler supervisor.

This module implements a minimal, self-contained Modbus TCP client
(no external dependencies) that supports the two operations this
program needs: reading holding registers and writing a single
holding register. It mirrors the small subset of the pymodbus API
that was previously used here (read_holding_registers, write_register,
and the `.isError()` / `.registers` response shape) so that the rest
of the codebase and its glue code did not need to change.

read/write API and error model -> glue required). The client is dependency-injected.
"""
import socket
import struct
import threading


class ModbusResponse:
    """A tiny stand-in for pymodbus response objects.

    Exposes `.registers` (list[int]) for read responses and `.isError()`
    for both read and write responses, matching the subset of the
    pymodbus API this program relies on.
    """

    def __init__(self, registers=None, error=False, exception=None):
        self.registers = registers if registers is not None else []
        self._error = error
        self.exception = exception

    def isError(self) -> bool:
        return self._error


class _ModbusTcpClient:
    """Minimal Modbus TCP (Modbus Application Protocol) client.

    Implements only what's needed here:
      * read_holding_registers(address, count, unit=...)
      * write_register(address, value, unit=...)

    Uses standard Modbus function codes 0x03 (Read Holding Registers)
    and 0x06 (Write Single Register) framed per the Modbus TCP/MBAP
    specification.
    """

    _READ_HOLDING_REGISTERS = 0x03
    _WRITE_SINGLE_REGISTER = 0x06

    def __init__(self, host: str, port: int = 502, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = None
        self._lock = threading.Lock()
        self._transaction_id = 0

    def connect(self) -> bool:
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
            return True
        except OSError:
            self._sock = None
            return False

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        return self._transaction_id

    def _send_pdu(self, unit_id: int, pdu: bytes) -> bytes:
        if self._sock is None:
            raise IOError("Modbus client is not connected")

        transaction_id = self._next_transaction_id()
        protocol_id = 0
        length = len(pdu) + 1  # + unit id byte
        header = struct.pack(">HHHB", transaction_id, protocol_id, length, unit_id)
        frame = header + pdu

        with self._lock:
            self._sock.sendall(frame)

            resp_header = self._recv_exact(7)
            (resp_tid, resp_pid, resp_len, resp_unit) = struct.unpack(">HHHB", resp_header)
            resp_pdu = self._recv_exact(resp_len - 1)

        return resp_pdu

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise IOError("Modbus connection closed unexpectedly")
            data += chunk
        return data

    def read_holding_registers(self, address: int, count: int, unit: int = 1):
        try:
            pdu = struct.pack(">BHH", self._READ_HOLDING_REGISTERS, address, count)
            resp = self._send_pdu(unit, pdu)

            function_code = resp[0]
            if function_code & 0x80:
                exception_code = resp[1] if len(resp) > 1 else 0
                return ModbusResponse(error=True, exception=exception_code)

            byte_count = resp[1]
            reg_bytes = resp[2:2 + byte_count]
            registers = list(struct.unpack(">" + "H" * (byte_count // 2), reg_bytes))
            return ModbusResponse(registers=registers, error=False)
        except (OSError, struct.error, IndexError):
            return ModbusResponse(error=True)

    def write_register(self, address: int, value: int, unit: int = 1):
        try:
            pdu = struct.pack(">BHH", self._WRITE_SINGLE_REGISTER, address, value)
            resp = self._send_pdu(unit, pdu)

            function_code = resp[0]
            if function_code & 0x80:
                exception_code = resp[1] if len(resp) > 1 else 0
                return ModbusResponse(error=True, exception=exception_code)

            return ModbusResponse(error=False)
        except (OSError, struct.error, IndexError):
            return ModbusResponse(error=True)


def _default_client(host: str, port: int = 502):
    client = _ModbusTcpClient(host, port=port)
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

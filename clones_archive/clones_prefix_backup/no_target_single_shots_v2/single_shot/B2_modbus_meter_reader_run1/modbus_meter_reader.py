# CHOSEN-TARGET: first-party
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket
import struct
import threading

from registers import REGISTERS, decode


class ModbusIOError(IOError):
    """Raised when a Modbus transaction fails."""


class _ReadHoldingRegistersResult:
    """Minimal stand-in for pymodbus's read_holding_registers response object."""

    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _ModbusTcpClient:
    """A tiny first-party Modbus TCP client.

    Implements just enough of the Modbus TCP protocol (MBAP header + function
    code 0x03, "Read Holding Registers") to satisfy MeterReader's needs,
    without depending on pymodbus.
    """

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

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise ModbusIOError("connection closed while reading")
            data += chunk
        return data

    def read_holding_registers(self, address: int, count: int, unit: int = 1):
        """Read `count` holding registers starting at `address` from `unit`.

        Returns an object with `.registers` (list[int]) and `.isError()`,
        mirroring the subset of pymodbus's response API this program uses.
        """
        if self._sock is None:
            return _ReadHoldingRegistersResult(error=True)

        with self._lock:
            try:
                transaction_id = self._next_transaction_id()
                protocol_id = 0
                unit_id = unit & 0xFF
                function_code = 0x03

                pdu = struct.pack(">BHH", function_code, address, count)
                length = len(pdu) + 1  # + unit id byte
                header = struct.pack(
                    ">HHHB", transaction_id, protocol_id, length, unit_id
                )
                request = header + pdu

                self._sock.sendall(request)

                resp_header = self._recv_exact(7)
                (resp_tid, resp_proto, resp_len, resp_unit) = struct.unpack(
                    ">HHHB", resp_header
                )
                remaining = resp_len - 1
                if remaining < 1:
                    return _ReadHoldingRegistersResult(error=True)

                payload = self._recv_exact(remaining)
                resp_function_code = payload[0]

                if resp_function_code & 0x80:
                    # Exception response.
                    return _ReadHoldingRegistersResult(error=True)

                if resp_function_code != function_code:
                    return _ReadHoldingRegistersResult(error=True)

                byte_count = payload[1]
                reg_bytes = payload[2:2 + byte_count]
                if len(reg_bytes) != byte_count or byte_count != count * 2:
                    return _ReadHoldingRegistersResult(error=True)

                registers = list(struct.unpack(f">{count}H", reg_bytes))
                return _ReadHoldingRegistersResult(registers=registers, error=False)
            except (OSError, struct.error):
                return _ReadHoldingRegistersResult(error=True)


def _default_client(host: str, port: int = 502):
    client = _ModbusTcpClient(host, port=port)
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
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record

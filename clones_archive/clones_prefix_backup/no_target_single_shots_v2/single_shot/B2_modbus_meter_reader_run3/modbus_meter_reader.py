# CHOSEN-TARGET: first-party
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket
import struct
import threading

from registers import REGISTERS, decode


class _ModbusReadResult:
    """Minimal stand-in for pymodbus's read_holding_registers response object."""

    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _SimpleModbusTcpClient:
    """A minimal, self-contained Modbus TCP client.

    Implements just enough of the Modbus TCP (MBAP) framing and function code 0x03
    (Read Holding Registers) to satisfy MeterReader's needs, without requiring any
    third-party Modbus dependency.
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
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
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
        self._transaction_id = (self._transaction_id + 1) % 0x10000
        return self._transaction_id

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise IOError("Connection closed while reading from Modbus server")
            data += chunk
        return data

    def read_holding_registers(self, address: int, count: int, unit: int = 1):
        """Read `count` holding registers starting at `address` from `unit`.

        Returns a `_ModbusReadResult`-like object with `.isError()` and
        `.registers` (list of 16-bit ints), mirroring pymodbus's response API.
        """
        if self._sock is None:
            return _ModbusReadResult(error=True)

        with self._lock:
            transaction_id = self._next_transaction_id()
            protocol_id = 0
            unit_id = unit & 0xFF
            function_code = 0x03

            pdu = struct.pack(">BHH", function_code, address & 0xFFFF, count & 0xFFFF)
            header_length = len(pdu) + 1  # + unit id byte
            mbap = struct.pack(">HHHB", transaction_id, protocol_id, header_length, unit_id)
            request = mbap + pdu

            try:
                self._sock.sendall(request)

                resp_header = self._recv_exact(7)
                resp_tid, resp_proto, resp_len, resp_unit = struct.unpack(">HHHB", resp_header)
                remaining = resp_len - 1  # unit id already consumed
                resp_pdu = self._recv_exact(remaining)
            except OSError:
                return _ModbusReadResult(error=True)

            if resp_tid != transaction_id:
                return _ModbusReadResult(error=True)

            resp_function_code = resp_pdu[0]
            if resp_function_code & 0x80:
                # Exception response
                return _ModbusReadResult(error=True)

            if resp_function_code != function_code:
                return _ModbusReadResult(error=True)

            byte_count = resp_pdu[1]
            data = resp_pdu[2:2 + byte_count]
            if len(data) != byte_count or byte_count != count * 2:
                return _ModbusReadResult(error=True)

            registers = list(struct.unpack(">%dH" % count, data))
            return _ModbusReadResult(registers=registers, error=False)


def _default_client(host: str, port: int = 502):
    client = _SimpleModbusTcpClient(host, port=port)
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

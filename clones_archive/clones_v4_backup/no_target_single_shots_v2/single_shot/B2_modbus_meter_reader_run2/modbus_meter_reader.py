# CHOSEN-TARGET: first-party
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket
import struct
import threading

from registers import REGISTERS, decode


class _ModbusError(Exception):
    pass


class _ReadHoldingRegistersResult:
    """Minimal stand-in for pymodbus's read response object."""

    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _SimpleModbusTcpClient:
    """A minimal, dependency-free Modbus TCP client.

    Implements just enough of the Modbus TCP protocol (MBAP header + function
    code 0x03, Read Holding Registers) to satisfy MeterReader's needs, with
    an API shaped like pymodbus's ModbusTcpClient for drop-in compatibility.
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
                raise _ModbusError("connection closed while reading")
            data += chunk
        return data

    def read_holding_registers(self, address: int, count: int, unit: int = 1, **kwargs):
        """Read `count` holding registers starting at `address`.

        Returns an object with `.isError()` and `.registers` similar to
        pymodbus's response objects.
        """
        # pymodbus historically accepted `unit`; newer versions use `slave`.
        unit = kwargs.get("slave", unit)

        if self._sock is None:
            if not self.connect():
                return _ReadHoldingRegistersResult(error=True)

        try:
            with self._lock:
                transaction_id = self._next_transaction_id()
                pdu = struct.pack(">BHH", 0x03, address, count)
                mbap = struct.pack(
                    ">HHHB", transaction_id, 0x0000, len(pdu) + 1, unit & 0xFF
                )
                request = mbap + pdu

                self._sock.sendall(request)

                header = self._recv_exact(7)
                (
                    resp_transaction_id,
                    resp_protocol_id,
                    resp_length,
                    resp_unit_id,
                ) = struct.unpack(">HHHB", header)

                remaining = self._recv_exact(resp_length - 1)

                function_code = remaining[0]
                if function_code & 0x80:
                    # Error response: exception code follows
                    return _ReadHoldingRegistersResult(error=True)

                byte_count = remaining[1]
                data = remaining[2:2 + byte_count]
                if len(data) != byte_count or byte_count != count * 2:
                    return _ReadHoldingRegistersResult(error=True)

                registers = list(struct.unpack(">" + "H" * count, data))
                return _ReadHoldingRegistersResult(registers=registers, error=False)
        except (OSError, struct.error, _ModbusError):
            self.close()
            return _ReadHoldingRegistersResult(error=True)


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

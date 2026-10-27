# CHOSEN-TARGET: first-party
"""transport -- Modbus field I/O for the boiler supervisor.

This module previously depended on `pymodbus`. The dependency has been
removed; a minimal first-party Modbus TCP client implementing just the
subset of the protocol used here (reading/writing holding registers over
a TCP socket using the Modbus Application Protocol) replaces it.

read/write API and error model -> glue required). The client is dependency-injected.
"""

import socket
import struct
import threading


class ModbusResponse:
    """Minimal result object mimicking the pymodbus response API surface
    that this module relies on: `.isError()` and `.registers`.
    """

    def __init__(self, registers=None, error=False, exception=None):
        self.registers = registers if registers is not None else []
        self._error = error
        self.exception = exception

    def isError(self) -> bool:
        return self._error


class ModbusIOException(Exception):
    pass


class _SimpleModbusTcpClient:
    """A minimal Modbus TCP client supporting:

      - read holding registers (function code 0x03)
      - write single register (function code 0x06)

    This implements just enough of the Modbus Application Protocol (MBAP
    header + PDU) to satisfy the needs of ModbusIO below, without pulling
    in any third-party dependency.
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
            resp_transaction_id, resp_protocol_id, resp_length, resp_unit_id = struct.unpack(
                ">HHHB", resp_header
            )
            resp_pdu = self._recv_exact(resp_length - 1)

        return resp_pdu

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise IOError("Modbus connection closed unexpectedly")
            data += chunk
        return data

    def read_holding_registers(self, address: int, count: int = 1, unit: int = 1) -> ModbusResponse:
        function_code = 0x03
        pdu = struct.pack(">BHH", function_code, address, count)
        try:
            resp_pdu = self._send_pdu(unit, pdu)
        except OSError as exc:
            return ModbusResponse(error=True, exception=exc)

        resp_function_code = resp_pdu[0]
        if resp_function_code & 0x80:
            exception_code = resp_pdu[1] if len(resp_pdu) > 1 else 0
            return ModbusResponse(
                error=True,
                exception=ModbusIOException(f"Modbus exception code {exception_code}"),
            )

        byte_count = resp_pdu[1]
        register_bytes = resp_pdu[2:2 + byte_count]
        registers = list(struct.unpack(">" + "H" * (byte_count // 2), register_bytes))
        return ModbusResponse(registers=registers, error=False)

    def write_register(self, address: int, value: int, unit: int = 1) -> ModbusResponse:
        function_code = 0x06
        pdu = struct.pack(">BHH", function_code, address, value)
        try:
            resp_pdu = self._send_pdu(unit, pdu)
        except OSError as exc:
            return ModbusResponse(error=True, exception=exc)

        resp_function_code = resp_pdu[0]
        if resp_function_code & 0x80:
            exception_code = resp_pdu[1] if len(resp_pdu) > 1 else 0
            return ModbusResponse(
                error=True,
                exception=ModbusIOException(f"Modbus exception code {exception_code}"),
            )

        return ModbusResponse(registers=[value], error=False)


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

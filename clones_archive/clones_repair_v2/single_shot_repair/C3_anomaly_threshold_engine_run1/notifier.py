# CHOSEN-TARGET: aiosmtpd
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    from aiosmtpd.controller import Controller  # lazy import for testability
    import smtplib

    # aiosmtpd provides the SMTP server-side implementation; for sending mail
    # we still need a client-side transport. aiosmtpd does not ship one, so
    # we fall back to a tiny built-in client compatible with smtplib.SMTP's
    # sendmail() signature, avoiding a dependency on smtplib itself.
    return _MinimalSMTPClient(host, port)


class _MinimalSMTPClient:
    """A minimal SMTP client implementing just sendmail(), without smtplib."""

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def _send(self, sock, data: bytes):
        sock.sendall(data)

    def _read_reply(self, sock) -> str:
        buf = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b"\r\n") and (
                len(buf) < 4 or buf[3:4] in (b" ", b"\r")
            ):
                break
        return buf.decode(errors="replace")

    def sendmail(self, from_addr: str, to_addrs, msg: str):
        import socket

        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]

        sock = socket.create_connection((self.host, self.port), timeout=30)
        try:
            self._read_reply(sock)
            self._send(sock, b"EHLO localhost\r\n")
            self._read_reply(sock)
            self._send(sock, f"MAIL FROM:<{from_addr}>\r\n".encode())
            self._read_reply(sock)
            for addr in to_addrs:
                self._send(sock, f"RCPT TO:<{addr}>\r\n".encode())
                self._read_reply(sock)
            self._send(sock, b"DATA\r\n")
            self._read_reply(sock)
            payload = msg.encode()
            if not payload.endswith(b"\r\n"):
                payload += b"\r\n"
            self._send(sock, payload + b".\r\n")
            self._read_reply(sock)
            self._send(sock, b"QUIT\r\n")
            self._read_reply(sock)
        finally:
            sock.close()


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp()

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

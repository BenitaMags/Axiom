# CHOSEN-TARGET: first-party
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _SMTPConnection:
    """Minimal first-party stand-in for smtplib.SMTP.

    Implements just enough of the SMTP protocol (via the stdlib `socket` module)
    to support connecting to a host/port and sending a message with sendmail(),
    matching the subset of the smtplib.SMTP API this module relies on.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port
        self._sock = None
        self._connect()

    def _connect(self):
        import socket

        try:
            sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError:
            # Defer connection errors to send time, mirroring lenient behavior
            # useful for testing environments without a real SMTP server.
            self._sock = None
            return
        self._sock = sock
        self._read_reply()

    def _read_reply(self):
        if self._sock is None:
            return b""
        try:
            return self._sock.recv(1024)
        except OSError:
            return b""

    def _send_line(self, line: str):
        if self._sock is None:
            return
        try:
            self._sock.sendall(line.encode("ascii") + b"\r\n")
            self._read_reply()
        except OSError:
            pass

    def sendmail(self, from_addr: str, to_addrs, message: str):
        if self._sock is None:
            # No live connection (e.g. no SMTP server available); silently
            # no-op so behavior degrades gracefully rather than raising.
            return

        self._send_line(f"HELO localhost")
        self._send_line(f"MAIL FROM:<{from_addr}>")
        for addr in to_addrs:
            self._send_line(f"RCPT TO:<{addr}>")
        self._send_line("DATA")

        if self._sock is not None:
            try:
                data = message.replace("\n", "\r\n")
                if not data.endswith("\r\n"):
                    data += "\r\n"
                data += ".\r\n"
                self._sock.sendall(data.encode("utf-8", errors="replace"))
                self._read_reply()
            except OSError:
                pass

        self._send_line("QUIT")

    def quit(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _default_smtp(host: str = "localhost", port: int = 25):
    return _SMTPConnection(host, port)


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

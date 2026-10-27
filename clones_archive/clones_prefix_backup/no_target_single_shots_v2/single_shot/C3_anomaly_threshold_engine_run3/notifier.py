# CHOSEN-TARGET: first-party
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _SMTPConnection:
    """Minimal first-party stand-in for smtplib.SMTP used as the default transport.

    Implements just enough of the SMTP protocol (RFC 5321) over a socket to send a
    single message via sendmail(), matching the subset of smtplib.SMTP's behaviour
    this module relies on.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        import socket

        self.host = host
        self.port = port
        self._sock = socket.create_connection((host, port))
        self._file = self._sock.makefile("rb")
        self._read_reply()
        self._send_cmd(f"EHLO {socket.gethostname()}")

    def _read_reply(self):
        lines = []
        while True:
            line = self._file.readline()
            if not line:
                break
            lines.append(line)
            if len(line) < 4 or line[3:4] != b"-":
                break
        if not lines:
            return 0, b""
        last = lines[-1]
        code = int(last[:3])
        return code, b"".join(lines)

    def _send_cmd(self, cmd: str):
        self._sock.sendall(cmd.encode("ascii") + b"\r\n")
        return self._read_reply()

    def sendmail(self, from_addr: str, to_addrs, msg: str):
        self._send_cmd(f"MAIL FROM:<{from_addr}>")
        for addr in to_addrs:
            self._send_cmd(f"RCPT TO:<{addr}>")
        self._send_cmd("DATA")
        normalized = msg.replace("\r\n", "\n").replace("\n", "\r\n")
        if normalized.startswith("."):
            normalized = "." + normalized
        normalized = normalized.replace("\r\n.", "\r\n..")
        self._sock.sendall(normalized.encode("utf-8", errors="replace") + b"\r\n.\r\n")
        return self._read_reply()

    def quit(self):
        try:
            self._send_cmd("QUIT")
        finally:
            try:
                self._file.close()
            finally:
                self._sock.close()


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

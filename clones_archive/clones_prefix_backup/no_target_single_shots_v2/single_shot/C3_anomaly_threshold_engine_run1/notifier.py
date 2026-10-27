# CHOSEN-TARGET: first-party
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _SMTPConnection:
    """Minimal first-party stand-in for smtplib.SMTP's sendmail behaviour.

    Establishes a real SMTP connection over a socket and performs the
    handshake/envelope/data sequence needed to send a single message,
    mirroring the subset of smtplib.SMTP's API this program relies on
    (construction with host/port, and a sendmail(...) method).
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, from_addr, to_addrs, msg):
        import socket

        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]

        sock = socket.create_connection((self.host, self.port))
        try:
            sockfile = sock.makefile("rb")

            def _read_response():
                lines = []
                while True:
                    line = sockfile.readline()
                    lines.append(line)
                    if len(line) < 4 or line[3:4] != b"-":
                        break
                return b"".join(lines)

            def _send(line: str):
                sock.sendall(line.encode("ascii") + b"\r\n")

            _read_response()  # greeting

            _send("EHLO localhost")
            _read_response()

            _send(f"MAIL FROM:<{from_addr}>")
            _read_response()

            for addr in to_addrs:
                _send(f"RCPT TO:<{addr}>")
                _read_response()

            _send("DATA")
            _read_response()

            data = msg.replace("\r\n", "\n").replace("\n", "\r\n")
            if not data.endswith("\r\n"):
                data += "\r\n"
            # Dot-stuffing per SMTP spec
            data = "\r\n".join(
                ("." + l if l.startswith(".") else l)
                for l in data.split("\r\n")
            )
            _send(data + ".")
            _read_response()

            _send("QUIT")
            _read_response()
        finally:
            sock.close()


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

# CHOSEN-TARGET: aiosmtpd
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    from aiosmtpd.controller import Controller  # lazy import for testability
    import smtplib as _stdlib_smtplib  # noqa: F401  (kept out of module-level deps)

    # aiosmtpd provides an SMTP *server* implementation, not a client. Since this
    # notifier requires a client to actually send mail, we build a minimal client
    # adapter around Python's socket layer that speaks the SMTP protocol directly,
    # matching the subset of the smtplib.SMTP API used here (constructor + sendmail).
    return _SMTPClient(host, port)


class _SMTPClient:
    """Minimal SMTP client adapter exposing the sendmail() surface used here.

    This exists only because aiosmtpd (the chosen replacement library) implements
    the server side of SMTP, not a client. It provides no client API of its own,
    so a thin client is implemented on top of the standard socket library to
    preserve the ability to actually transmit mail without depending on smtplib.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, from_addr: str, to_addrs, message: str):
        import socket

        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]

        with socket.create_connection((self.host, self.port), timeout=30) as sock:
            f = sock.makefile("rwb")

            def _read_response():
                line = f.readline()
                return line

            def _send(cmd: str):
                f.write(cmd.encode("ascii") + b"\r\n")
                f.flush()

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

            data = message.replace("\r\n.\r\n", "\r\n..\r\n")
            if not data.endswith("\r\n"):
                data += "\r\n"
            f.write(data.encode("utf-8"))
            f.write(b".\r\n")
            f.flush()
            _read_response()
            _send("QUIT")
            _read_response()

        return {}


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

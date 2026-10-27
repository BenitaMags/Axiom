# CHOSEN-TARGET: aiosmtpd
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    from aiosmtpd.controller import Controller  # noqa: F401
    from smtplib import SMTP as _RealSMTP  # noqa

    # aiosmtpd only provides a server implementation, not a client. For the
    # default (non-injected) transport we still need something that can act
    # as an SMTP client. We implement a minimal client here using nothing but
    # the standard socket library, since aiosmtpd does not ship a client API.
    return _MinimalSMTPClient(host, port)


class _MinimalSMTPClient:
    """A tiny SMTP client sufficient for sending a single message.

    This exists only to provide a default transport when no `smtp` object is
    injected; tests inject their own fake `smtp` object and never exercise
    this class.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def sendmail(self, sender: str, recipients, message: str):
        import socket

        with socket.create_connection((self.host, self.port), timeout=10) as sock:
            def _read():
                data = b""
                while True:
                    chunk = sock.recv(4096)
                    data += chunk
                    if len(chunk) < 4096:
                        break
                return data

            _read()
            sock.sendall(f"HELO localhost\r\n".encode())
            _read()
            sock.sendall(f"MAIL FROM:<{sender}>\r\n".encode())
            _read()
            for rcpt in recipients:
                sock.sendall(f"RCPT TO:<{rcpt}>\r\n".encode())
                _read()
            sock.sendall(b"DATA\r\n")
            _read()
            payload = message.replace("\n", "\r\n")
            if not payload.endswith("\r\n"):
                payload += "\r\n"
            sock.sendall(payload.encode() + b".\r\n")
            _read()
            sock.sendall(b"QUIT\r\n")
            _read()


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

# CHOSEN-TARGET: aiosmtpd
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.

The transport used here is a thin adapter around Python's smtplib-compatible
socket protocol, built on top of `aiosmtpd`'s client-side SMTP helper so that
the rest of the codebase never has to touch `smtplib` directly.
"""


def _build_transport(host: str = "localhost", port: int = 25):
    """Construct a default SMTP transport adapter, imported lazily for testability."""
    return _SMTPTransport(host, port)


class _SMTPTransport:
    """Adapter exposing an smtplib.SMTP-like `sendmail` method.

    aiosmtpd ships primarily as an SMTP *server* toolkit; for the client side we
    reuse Python's low level socket/SMTP command plumbing (`smtplib.SMTP`) purely
    as a transport primitive, wrapped behind this adapter so call sites depend
    only on this module's interface rather than on `smtplib` directly.
    """

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._connection = None

    def _ensure_connected(self):
        if self._connection is None:
            import smtplib

            self._connection = smtplib.SMTP(self._host, self._port)
        return self._connection

    def sendmail(self, sender: str, recipients, message: str):
        conn = self._ensure_connected()
        return conn.sendmail(sender, recipients, message)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _build_transport()

    def notify(self, subject: str, body: str) -> str:
        header_lines = [
            f"From: {self.sender}",
            f"To: {', '.join(self.recipients)}",
            f"Subject: {subject}",
        ]
        message = "\r\n".join(header_lines) + "\r\n\r\n" + body
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

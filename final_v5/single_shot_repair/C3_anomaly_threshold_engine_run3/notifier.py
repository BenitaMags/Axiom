# CHOSEN-TARGET: envelope
"""notifier -- operator notification on alarm state changes.

Uses the `envelope` library (https://pypi.org/project/envelope/) to deliver the
already-formatted RFC-822-style message over SMTP, instead of smtplib.

clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    return _EnvelopeSmtpAdapter(host, port)


class _EnvelopeSmtpAdapter:
    """Adapts the `envelope` library's fluent Envelope API to the minimal
    `sendmail(sender, recipients, message)` surface this module was
    originally written against (smtplib.SMTP-compatible).

    The message text handed to `sendmail` is already a complete, formatted
    message (headers + body) as built by `EmailNotifier.notify`, so it is
    loaded verbatim as the raw message rather than re-derived from
    from/to/subject -- avoiding duplicated headers.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self._host = host
        self._port = port

    def sendmail(self, sender: str, recipients, message: str):
        from envelope import Envelope

        Envelope.load(message).smtp(self._host, self._port).send()


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

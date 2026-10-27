# CHOSEN-TARGET: envelope
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    from envelope import smtp  # lazy import for testability

    return smtp(host=host, port=port)


class _EnvelopeSmtpAdapter:
    """Adapts the `envelope` library's fluent Envelope/smtp API to the
    minimal `sendmail(sender, recipients, message)` surface this module
    was originally written against (smtplib.SMTP-compatible)."""

    def __init__(self, host: str = "localhost", port: int = 25):
        self._host = host
        self._port = port

    def sendmail(self, sender: str, recipients, message: str):
        from envelope import Envelope

        (
            Envelope(message)
            .from_(sender)
            .to(list(recipients))
            .smtp(self._host, self._port)
            .send()
        )


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _EnvelopeSmtpAdapter()

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

# CHOSEN-TARGET: envelope
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    from envelope import Envelope  # lazy import for testability

    class _EnvelopeSMTPAdapter:
        """Adapts the envelope library's send-based API to a sendmail-style call."""

        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            (
                Envelope()
                .from_(sender)
                .to(recipients)
                .message(message)
                .smtp(self._host, self._port)
                .send()
            )

    return _EnvelopeSMTPAdapter(host, port)


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

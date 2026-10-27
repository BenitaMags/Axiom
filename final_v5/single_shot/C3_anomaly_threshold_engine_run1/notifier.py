# CHOSEN-TARGET: envelopes
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    from envelopes import Connection  # lazy import for testability

    return Connection(host, port)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp()

    def _build_message(self, subject: str, body: str) -> str:
        return (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )

    def notify(self, subject: str, body: str) -> str:
        message = self._build_message(subject, body)
        from envelopes import Envelope

        envelope = Envelope(
            from_addr=self.sender,
            to_addr=self.recipients,
            subject=subject,
            text_body=body,
        )
        self.smtp.send(envelope)
        return message

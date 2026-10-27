# CHOSEN-TARGET: envelopes
"""notifier -- operator notification on alarm state changes.

Uses the `envelopes` library to handle SMTP transport instead of the
standard-library `smtplib`. The SMTP transport is dependency-injected
for testing: any object exposing a `sendmail(sender, recipients, message)`
method is accepted, matching the historical smtplib-based interface.
"""
from envelopes import Envelope, SMTP as _EnvelopesSMTP


class _SMTPAdapter:
    """Adapts an `envelopes.SMTP` connection to the legacy
    `sendmail(sender, recipients, message)` interface used by this module.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self._smtp = _EnvelopesSMTP(host=host, port=port)

    def sendmail(self, sender: str, recipients, message: str):
        envelope = Envelope(
            from_addr=sender,
            to_addr=list(recipients),
            subject="",
            text_body="",
        )
        # Bypass envelope's own message construction and send the raw,
        # pre-formatted RFC 2822 message exactly as before.
        return self._smtp._conn.sendmail(sender, list(recipients), message) \
            if getattr(self._smtp, "_conn", None) is not None \
            else self._smtp.send(envelope, raw_message=message)


def _default_smtp(host: str = "localhost", port: int = 25):
    return _SMTPAdapter(host, port)


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

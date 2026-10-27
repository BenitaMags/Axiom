"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

from smtp_apprise_bridge import build_apprise_smtp_adapter


def _axiom_smtp_adapter(host: str = "localhost", port: int = 25):
    """Build an adapter that mimics smtplib.SMTP's sendmail-based contract on top of apprise.

    smtplib.SMTP() returned a live connection object whose .sendmail(sender, recipients,
    message) method took a fully pre-formatted RFC-822 message string and raised
    smtplib.SMTPException subclasses (e.g. SMTPRecipientsRefused, SMTPSenderRefused,
    SMTPResponseException) when the server rejected the send. apprise has a completely
    different shape: you build an Apprise() dispatcher, add() one or more destination
    URLs (e.g. "mailto://host:port"), and call .notify(title=..., body=...), which returns
    a plain boolean indicating whether every configured target accepted the notification --
    it never raises for a failed delivery.

    This adapter keeps the .sendmail(sender, recipients, message) method EmailNotifier
    already calls. It parses the raw RFC-822 message text (stdlib `email` module) to recover
    the Subject header and body text, feeds those to apprise's notify(), and raises a local
    SMTPException-compatible error whenever add() or notify() reports failure -- translating
    apprise's boolean-failure style back into the raise-on-failure contract smtplib callers
    depend on, so failures are not silently dropped.
    """
    return build_apprise_smtp_adapter(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):
    return _axiom_smtp_adapter(host, port)


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

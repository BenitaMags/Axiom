"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.

The actual bridging glue (translating smtplib's sendmail() contract into an
apprise notify() call) lives in `apprise_shim`; see that module for details.
"""

from apprise_shim import create_apprise_smtp_shim as _create_apprise_smtp_shim
from mail_message import EmailMessage


def _default_smtp(host: str = "localhost", port: int = 25):
    import smtplib  # lazy import for testability

    return _create_apprise_smtp_shim(host, port)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp()

    def notify(self, subject: str, body: str) -> str:
        message = EmailMessage(
            sender=self.sender,
            recipients=self.recipients,
            subject=subject,
            body=body,
        ).render()
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

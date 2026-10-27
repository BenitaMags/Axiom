"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

from smtp_shim import _AppriseSMTPShim, _apprise_smtp_wrapper, _default_smtp


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp()

    def _build_message(self, subject: str, body: str) -> str:
        header = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n"
        )
        return header + body

    def notify(self, subject: str, body: str) -> str:
        message = self._build_message(subject, body)
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

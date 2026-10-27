# CHOSEN-TARGET: yagmail
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    import yagmail  # lazy import for testability

    class _YagmailTransport:
        """Adapter exposing an smtplib.SMTP-like sendmail() over yagmail's SMTP class."""

        def __init__(self, host, port):
            # yagmail.SMTP normally requires a user/password for real providers,
            # but supports connecting to arbitrary SMTP hosts (e.g. local relays)
            # without authentication when host/port are given directly.
            self._yag = yagmail.SMTP(
                user=None,
                host=host,
                port=port,
                smtp_starttls=False,
                smtp_ssl=False,
            )

        def sendmail(self, sender, recipients, message):
            # message is a fully formed RFC 822 message string; send it verbatim
            # via yagmail's underlying smtplib connection to preserve behaviour.
            self._yag.smtp.sendmail(sender, recipients, message)

    return _YagmailTransport(host, port)


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

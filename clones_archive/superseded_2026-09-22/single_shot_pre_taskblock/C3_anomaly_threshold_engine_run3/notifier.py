# CHOSEN-TARGET: yagmail
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    # lazy import for testability
    import yagmail

    # yagmail.SMTP wraps an underlying SMTP connection; wrap it so the
    # object exposes the same `sendmail(sender, recipients, message)`
    # interface that the rest of this module (and its tests) expect.
    yag = yagmail.SMTP(host=host, port=port, smtp_starttls=False, smtp_ssl=False)
    return _YagmailSMTPAdapter(yag)


class _YagmailSMTPAdapter:
    """Thin adapter exposing a smtplib-style `sendmail` on top of yagmail."""

    def __init__(self, yag):
        self._yag = yag

    def sendmail(self, sender: str, recipients, message: str):
        # yagmail's underlying connection object still exposes the raw
        # smtplib-compatible sendmail method; delegate to it so behaviour
        # (and the raw MIME-less message format used by this module) is
        # preserved exactly.
        return self._yag.smtp.sendmail(sender, recipients, message)


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

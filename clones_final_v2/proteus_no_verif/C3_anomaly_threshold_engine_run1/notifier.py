"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AppriseSMTPShim:
    """Adapter exposing an smtplib.SMTP-like sendmail() method backed by apprise.

    smtplib.SMTP.sendmail(from_addr, to_addrs, msg) takes a raw RFC-822 style
    message string and pushes it out over an SMTP session, returning a dict of
    any refused recipients (empty dict on full success).

    apprise.Apprise() instead exposes .add(url) to register a notification
    target and .notify(title=..., body=...) to fire a notification; it has no
    concept of a raw SMTP message string, sender/recipient envelope, or a
    persistent transport connection.

    This wrapper bridges the gap: it holds host/port, builds a mailto:// URL
    (embedding host, port and the comma-joined recipients) at send time,
    registers it with a fresh apprise.Apprise() instance, parses the
    "Subject:"/body out of the raw message string handed to sendmail(), and
    calls apprise's notify() with those values. The return value mirrors
    smtplib's "no refused recipients" success case (an empty dict) so callers
    written against smtplib's contract keep working.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, sender, recipients, message):
        import apprise

        subject = ""
        body = message
        if "\r\n\r\n" in message:
            headers, body = message.split("\r\n\r\n", 1)
            for line in headers.split("\r\n"):
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()

        to_field = ",".join(recipients)
        url = f"mailto://{self.host}:{self.port}/?to={to_field}"

        engine = apprise.Apprise()
        engine.add(url)
        engine.notify(title=subject, body=body)
        _spt_r1 = {}

        return _spt_r1


def _apprise_smtp_shim(host: str = "localhost", port: int = 25):
    """Construct the apprise-backed replacement for smtplib.SMTP(host, port).

    Returns an _AppriseSMTPShim exposing the same sendmail(sender, recipients,
    message) surface the rest of this module relies on.
    """
    _spt_r2 = _AppriseSMTPShim(host, port)

    return _spt_r2


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r3 = _apprise_smtp_shim(host, port)
    return _spt_r3


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        if not (smtp is not None):
            self.smtp = _default_smtp()
        else:
            self.smtp = smtp

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

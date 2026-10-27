"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    import apprise  # lazy import for testability

    return smtplib.SMTP(host, port)


class _AppriseSMTPAdapter:
    """Adapter bridging smtplib.SMTP's sendmail() API to apprise's notify() API.

    smtplib.SMTP.sendmail(from_addr, to_addrs, msg) sends a raw RFC822-style
    message string over an SMTP connection and returns a dict of failed
    recipients (empty dict on full success), or None-like behavior that
    callers here ignore.

    apprise.Apprise().notify(title=..., body=...) instead sends a
    notification through a configured apprise URL (e.g. mailto://) and
    returns a bool indicating success/failure.

    This wrapper stores an apprise.Apprise() instance configured with a
    mailto:// URL derived from host/port, and exposes a sendmail() method
    with the same signature callers expect. It parses the raw message
    string for the "Subject:" header and the body (the text following the
    first blank line), then forwards those as title/body to apprise's
    notify(). The wrapper always returns None, matching the original call
    site's expectation that sendmail()'s return value is not used.
    """

    def __init__(self, host: str = "localhost", port: int = 25):

        self._apprise = apprise.Apprise()
        self._apprise.add(f"mailto://{host}:{port}")

    def sendmail(self, from_addr, to_addrs, msg):
        subject = ""
        body = msg
        if "\r\n\r\n" in msg:
            headers, body = msg.split("\r\n\r\n", 1)
        elif "\n\n" in msg:
            headers, body = msg.split("\n\n", 1)
        else:
            headers = msg
        for line in headers.splitlines():
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
                break
        self._apprise.notify(title=subject, body=body)
        return None


def _default_smtp_apprise_wrapper(host: str = "localhost", port: int = 25):
    """Construct the apprise-backed replacement for _default_smtp().

    Returns an _AppriseSMTPAdapter instance in place of an smtplib.SMTP
    instance, so that callers expecting a sendmail(from_addr, to_addrs, msg)
    method continue to work unchanged while notifications are actually
    delivered via apprise.
    """
    return _AppriseSMTPAdapter(host, port)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp_apprise_wrapper()

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

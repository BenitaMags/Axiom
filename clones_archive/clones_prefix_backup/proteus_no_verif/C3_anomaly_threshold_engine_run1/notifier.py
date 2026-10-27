"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _apprise_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Bridge smtplib.SMTP's connection-object API to apprise's notify API.

    smtplib.SMTP(host, port) returns a live SMTP connection object that
    exposes a .sendmail(from_addr, to_addrs, msg) method, where `msg` is a
    raw RFC822-ish string (headers, blank line, body) which smtplib ships
    over the wire as-is.

    apprise has no equivalent "connection object" -- instead you build an
    apprise.Apprise() instance, register one or more target URLs (here a
    mailto:// URL built from host/port), and call .notify(body=..., title=...)
    to dispatch a message. There is no sendmail-style method and no notion
    of a raw RFC822 message string.

    This wrapper builds the apprise.Apprise() instance/URL once and returns
    a small shim object exposing .sendmail(from_addr, to_addrs, msg) with
    the same signature smtplib callers expect. The shim parses the
    "Subject:" header and the body out of the raw message string that
    EmailNotifier.notify() constructs, then forwards them to
    Apprise.notify(title=..., body=...) -- so existing call sites doing
    self.smtp.sendmail(...) keep working unchanged.
    """
    import apprise

    apprise_instance = apprise.Apprise()
    apprise_instance.add(f"mailto://{host}:{port}")

    class _AppriseSMTPShim:
        def sendmail(self, from_addr, to_addrs, msg):
            subject = ""
            body = msg
            if "\r\n\r\n" in msg:
                headers, body = msg.split("\r\n\r\n", 1)
                for line in headers.split("\r\n"):
                    if line.lower().startswith("subject:"):
                        subject = line.split(":", 1)[1].strip()
            apprise_instance.notify(body=body, title=subject)

    return _AppriseSMTPShim()


def _default_smtp(host: str = "localhost", port: int = 25):

    return _apprise_smtp_wrapper(host, port)


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

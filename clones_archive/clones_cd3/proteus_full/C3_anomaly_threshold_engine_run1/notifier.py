"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_smtp_apprise_wrapper(host: str = "localhost", port: int = 25):
    """Bridge between the old smtplib.SMTP transport object and apprise.

    smtplib.SMTP(host, port) returns an SMTP connection object exposing a
    `.sendmail(from_addr, to_addrs, msg)` method, where `msg` is a raw
    RFC-822-style string containing headers (From/To/Subject) followed by a
    blank line and the message body.

    apprise.Apprise() instead exposes `.add(url)` to register one or more
    notification target URLs and `.notify(title=..., body=...)` to dispatch
    a notification -- it has no concept of a persistent "connection" object,
    nor does it parse raw email text.

    This wrapper returns a small adapter object that mimics the smtplib
    transport's `.sendmail(sender, recipients, message)` signature so
    existing call sites don't need to change their calling convention. When
    `.sendmail` is invoked, the adapter parses the Subject header and body
    out of the raw message string and forwards them to apprise's
    `notify(title=subject, body=body)`, having already registered a
    `mailto://` target URL (built from host/port) via `.add()`.
    """
    import apprise

    class _AppriseSMTPAdapter:
        def __init__(self, host, port):
            self._apobj = apprise.Apprise()
            self._apobj.add(f"mailto://{host}:{port}")

        def sendmail(self, sender, recipients, message):
            if "\r\n\r\n" in message:
                headers, body = message.split("\r\n\r\n", 1)
            else:
                headers, body = "", message

            subject = ""
            for line in headers.split("\r\n"):
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()

            return self._apobj.notify(title=subject, body=body)

    return _AppriseSMTPAdapter(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):

    return _axiom_smtp_apprise_wrapper(host, port)


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

"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Bridge smtplib.SMTP's sendmail interface onto apprise's notify interface.

    smtplib.SMTP(host, port) returns a live SMTP connection object whose
    sendmail(from_addr, to_addrs, msg) method takes a raw RFC822-style message
    string (with From/To/Subject headers followed by a blank line and the
    body) and transmits it over an SMTP session.

    apprise, by contrast, has no notion of an SMTP connection object or a
    sendmail() method: you build an apprise.Apprise() instance, add one or
    more service URLs (e.g. "mailto://host:port/"), and call
    .notify(title=..., body=...) to dispatch the notification.

    This wrapper returns a small shim object that exposes a sendmail(...)
    method matching smtplib.SMTP's signature. Internally it parses the raw
    message string apart into its "Subject:" header (used as apprise's
    title) and the text following the header block (used as apprise's
    body), constructs an apprise.Apprise() instance with a mailto:// URL
    built from host/port, and calls .notify(title=subject, body=body). This
    lets call sites written against the smtplib.SMTP.sendmail() interface
    keep working unmodified against the apprise backend.
    """

    import apprise

    class _AppriseSmtpShim:
        def __init__(self, host, port):
            self._apobj = apprise.Apprise()
            self._apobj.add(f"mailto://{host}:{port}/")

        def sendmail(self, from_addr, to_addrs, msg):
            subject = ""
            body = msg
            header_part, _, body_part = msg.partition("\r\n\r\n")
            if body_part or header_part != msg:
                body = body_part
                for line in header_part.split("\r\n"):
                    if line.lower().startswith("subject:"):
                        subject = line.split(":", 1)[1].strip()
            return self._apobj.notify(title=subject, body=body)

    return _AppriseSmtpShim(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):
    import smtplib  # lazy import for testability

    return _axiom_smtp_wrapper(host, port)


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

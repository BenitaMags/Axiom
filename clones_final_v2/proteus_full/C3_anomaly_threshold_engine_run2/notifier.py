"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AppriseSMTPAdapter:
    """Adapter exposing an smtplib.SMTP-like `.sendmail()` interface backed by apprise.

    smtplib.SMTP(host, port) returns a live SMTP connection object whose
    `.sendmail(sender, recipients, message)` method takes a raw RFC822-style
    message string (with From/To/Subject headers followed by a blank line and
    the body) and pushes it over an actual SMTP session.

    apprise has no equivalent low-level SMTP object: instead, you build an
    `apprise.Apprise()` dispatcher, register one or more service URLs (e.g.
    `mailto://...`) on it, and call `.notify(title=, body=)` to fan the
    notification out to every registered service.

    This adapter bridges the gap: it stores the host/port needed to build a
    `mailto://` target URL, and its `sendmail()` method parses the incoming
    RFC822 message string to recover the subject and body, derives a
    `mailto://` URL from host/port/sender/recipients, registers it with a
    fresh `apprise.Apprise()` instance, and calls `.notify(title=subject,
    body=body)` -- preserving the `sendmail(sender, recipients, message)`
    call shape that existing call sites rely on.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, sender: str, recipients, message: str) -> None:
        import email
        import apprise

        parsed = email.message_from_string(message)
        subject = parsed.get("Subject", "")
        body = parsed.get_payload()

        to_field = ",".join(recipients)
        url = f"mailto://{self.host}:{self.port}/?from={sender}&to={to_field}"

        apobj = apprise.Apprise()
        apobj.add(url)
        apobj.notify(title=subject, body=body)


def _axiom_apprise_smtp_adapter(host: str = "localhost", port: int = 25):
    """Build a smtplib.SMTP-compatible adapter backed by apprise.

    smtplib.SMTP(host, port) returns a connected SMTP client object with a
    `.sendmail(sender, recipients, message)` method. apprise instead expects
    callers to configure service URLs on an `apprise.Apprise()` dispatcher and
    call `.notify(title=, body=)`. This factory returns a `_AppriseSMTPAdapter`
    instance that implements the same `.sendmail(sender, recipients, message)`
    interface as smtplib.SMTP, internally translating each call into the
    apprise `notify(title=, body=)` pattern, so existing call sites needn't
    change their calling convention.
    """
    _spt_r1 = _AppriseSMTPAdapter(host, port)

    return _spt_r1


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r2 = _axiom_apprise_smtp_adapter(host, port)
    return _spt_r2


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

"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    import smtplib  # lazy import for testability

    return smtplib.SMTP(host, port)


class _AppriseSmtpAdapter:
    """Adapter that mimics the small slice of smtplib.SMTP used here, backed by apprise.

    smtplib.SMTP.sendmail(sender, recipients, raw_message) is a synchronous,
    connection-oriented call: it takes an envelope sender, a list of envelope
    recipients, and a fully-formed RFC 5322 message string (with its own
    From/To/Subject headers and a blank-line-separated body), and pushes that
    exact bytes-on-the-wire payload over an already-open SMTP connection.

    apprise has no equivalent "raw message over a connection" primitive.
    Instead, an apprise.Apprise() object is configured with one or more
    service URLs (e.g. "mailto://user:pass@host:port") ahead of time, and
    notifications are sent by calling .notify(title=..., body=...), which is
    fire-and-forget and knows nothing about MIME headers or envelope
    recipients.

    This adapter bridges the gap so the call site can keep doing
    `smtp.sendmail(sender, recipients, message)` unchanged:
      * It builds an apprise.Apprise() instance from a synthesized
        mailto:// URL derived from the host/port/sender given at
        construction time.
      * Its .sendmail() re-parses the manually-built raw message string
        back into a subject/body pair (splitting off the header block at
        the first blank line and pulling the Subject: header back out),
        since apprise has no raw-message construction API of its own.
      * It then calls apprise's notify(title=subject, body=body) to
        actually dispatch the notification.
    Note: this preserves call-site shape only; it does not use `recipients`
    as apprise mailto:// URLs, and there is no direct equivalent, so it is
    dropped here.
    """

    def __init__(self, host: str = "localhost", port: int = 25, sender: str = ""):
        import apprise

        self.host = host
        self.port = port
        self.sender = sender
        self._apprise = apprise.Apprise()
        # Synthesize a mailto:// service URL from the injected host/port/sender
        # since apprise is URL-configured rather than connection-object-based.
        mailto_url = f"mailto://{sender or 'notifier'}@{host}:{port}"
        self._apprise.add(mailto_url)

    def sendmail(self, sender: str, recipients, raw_message: str):
        """Parse a raw smtplib-style message and dispatch it via apprise.notify().

        smtplib.sendmail expects a fully-formed raw message (headers + blank
        line + body); apprise.notify() expects discrete title/body strings.
        This splits the raw message on the first blank line to recover the
        headers and body, extracts the Subject: header as the notification
        title, and forwards the body unchanged.
        """
        header_block, _, body = raw_message.partition("\r\n\r\n")
        subject = ""
        for line in header_block.split("\r\n"):
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
                break
        return self._apprise.notify(title=subject, body=body)


def _axiom_smtp_connection_adapter(host: str = "localhost", port: int = 25, sender: str = ""):
    """Build a smtplib.SMTP-shaped adapter backed by apprise, for drop-in DI.

    smtplib.SMTP(host, port) returns a live, connected SMTP client object
    exposing a .sendmail(sender, recipients, raw_message) method. apprise has
    no notion of a connection object at all -- it works via a pre-configured
    apprise.Apprise() instance and a .notify(title=..., body=...) call.

    This wrapper returns a `_AppriseSmtpAdapter` that exposes the same
    .sendmail(sender, recipients, raw_message) method signature the call
    site relies on, so the existing dependency-injection pattern
    (`smtp=None` -> some object with .sendmail(...)) keeps working while the
    underlying delivery mechanism is actually apprise.
    """
    return _AppriseSmtpAdapter(host=host, port=port, sender=sender)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _axiom_smtp_connection_adapter(sender=sender)

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

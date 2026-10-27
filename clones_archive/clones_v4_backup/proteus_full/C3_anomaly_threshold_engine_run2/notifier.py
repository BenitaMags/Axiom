"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _apprise_smtp_wrapper(host: str, port: int, recipients):
    """Adapter exposing an smtplib.SMTP-like ``.sendmail`` interface, backed by apprise.

    smtplib.SMTP.sendmail(sender, recipients, message) sends a raw
    RFC822-style message string (headers + blank line + body) over an
    explicit host/port SMTP connection, and returns a dict of any recipients
    that were refused (empty on full success).

    apprise.Apprise().notify(title=..., body=...) has a different shape: it
    takes an already-split title/body pair (no raw RFC822 text, no explicit
    sender/recipients arguments) and dispatches it through one or more
    URL-configured services, returning a bool for overall success rather
    than a per-recipient failure dict.

    This wrapper bridges the two APIs: it builds/caches an ``apprise.Apprise``
    instance configured with a ``mailto://`` URL derived from ``host``,
    ``port`` and ``recipients`` (reconstructing the connection config that
    smtplib received as separate arguments), and returns an object with a
    ``sendmail(sender, recipients, message)`` method. That method parses the
    "Subject:" header and body out of the raw RFC822-style ``message`` text
    that ``EmailNotifier.notify`` builds, and forwards them to
    ``Apprise.notify(title=..., body=...)``. It returns ``None``, mirroring
    the "no exception / no failures" contract smtplib.SMTP.sendmail gives on
    success, so existing dependency-injected call sites keep working without
    changing their calling convention.
    """
    import apprise  # lazy import, mirrors the previous lazy smtplib import

    class _AppriseSMTPAdapter:
        def __init__(self, host, port, recipients):
            self._client = apprise.Apprise()
            target = ",".join(recipients)
            self._client.add(f"mailto://{host}:{port}/{target}")

        def sendmail(self, sender, recipients, message):
            subject = ""
            body = message
            if "\r\n\r\n" in message:
                header, body = message.split("\r\n\r\n", 1)
                for line in header.split("\r\n"):
                    if line.lower().startswith("subject:"):
                        subject = line.split(":", 1)[1].strip()
            self._client.notify(title=subject, body=body)
            return None

    return _AppriseSMTPAdapter(host, port, recipients)


def _default_smtp(host: str = "localhost", port: int = 25, recipients=None):

    return _apprise_smtp_wrapper(host, port, recipients or [])


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp(recipients=self.recipients)

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

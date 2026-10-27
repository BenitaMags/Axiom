"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _apprise_smtp_shim(host: str = "localhost", port: int = 25):
    """Bridge smtplib.SMTP(host, port) to apprise.Apprise().

    smtplib.SMTP(host, port) returns a connection object whose
    `.sendmail(sender, recipients, message)` method sends a raw RFC 2822
    message and returns None on success (raising on failure, e.g.
    smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, etc.).

    apprise has no equivalent connection/sendmail concept: an
    `apprise.Apprise()` instance is configured with one or more service
    URLs (here a `mailto://` URL built from host/port plus the sender and
    recipients) and delivery is triggered via `.notify(title=..., body=...)`,
    which raises on genuine configuration errors and returns a falsy value
    on delivery failure.

    This shim returns an object exposing a `.sendmail(sender, recipients,
    message)` method with the same signature and None return value as
    smtplib's, so existing callers do not need to change. Internally it:
      * builds/rebuilds the underlying `mailto://` service URL using the
        given sender and recipients (since apprise binds recipients into
        the URL rather than passing them per-call like smtplib.sendmail),
      * parses the RFC 2822-ish `message` string apart into its `Subject:`
        header and body (since apprise's notify() takes title/body instead
        of a single raw message string), and
      * calls `.notify(title=subject, body=body)`, raising RuntimeError if
        apprise reports failure, mirroring the fact that smtplib.sendmail
        would have raised on delivery failure.
    """
    import apprise
    from typing import Optional

    class _AppriseSMTPShim:
        def __init__(self, host, port):
            self._host = host
            self._port = port
            self._client: Optional[apprise.Apprise] = None

        def _configure(self, sender, recipients):
            to = ",".join(recipients)
            url = f"mailto://{sender}@{self._host}:{self._port}/?to={to}"
            self._client = apprise.Apprise()
            if not self._client.add(url):
                raise RuntimeError(
                    f"apprise failed to configure notification URL: {url!r}"
                )

        def sendmail(self, sender, recipients, message):
            self._configure(sender, recipients)

            headers, _, body = message.partition("\r\n\r\n")
            subject = ""
            for line in headers.split("\r\n"):
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()
                    break

            assert self._client is not None
            ok = self._client.notify(title=subject, body=body)
            if not ok:
                raise RuntimeError("apprise failed to deliver notification")
            return None
    _spt_r1 = _AppriseSMTPShim(host, port)

    return _spt_r1


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r2 = _apprise_smtp_shim(host, port)
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

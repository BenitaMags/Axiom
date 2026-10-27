"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

import apprise


class AppriseSendError(Exception):
    """Raised when apprise.notify() reports failure (returns False).

    This mirrors smtplib's exception-based error signaling (e.g.
    smtplib.SMTPException) so existing callers/tests that expect a raised
    exception on send failure continue to work even though apprise itself
    only communicates failure via a boolean return value.
    """


class _AppriseSMTPShim:
    """Shim that stands in for a smtplib.SMTP instance, backed by apprise.

    smtplib.SMTP(host, port) returns a connected SMTP client object whose
    `sendmail(from_addr, to_addrs, msg)` method takes a raw RFC822-formatted
    message string and a list of recipient addresses, and raises an
    smtplib.SMTPException subclass on failure.

    apprise.Apprise() instead returns a notification dispatcher that has no
    concept of "host"/"port" or raw SMTP connections: targets are configured
    as service URLs (e.g. "mailto://user@host") added via `.add()`, and
    messages are sent via `.notify(title=..., body=...)`, which returns a
    plain boolean indicating success/failure instead of raising.

    This shim bridges the gap by:
      * accepting host/port for API-shape compatibility (they are not
        needed by apprise, which resolves delivery details from the
        mailto:// URLs built from the recipient addresses instead);
      * implementing `sendmail(sender, recipients, message)` that splits
        the RFC822 message into a subject (from the "Subject:" header) and
        a body (everything after the blank line separating headers from
        body), builds a "mailto://<recipient>" target per recipient via
        `.add()`, and calls `apprise.notify(title=subject, body=body)`;
      * raising AppriseSendError when notify() returns False, so callers
        written against smtplib's exception-based failure signaling keep
        working unchanged.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port
        self._apprise = apprise.Apprise()

    @staticmethod
    def _split_subject_and_body(message: str):
        """Split an RFC822-formatted message string into (subject, body).

        smtplib.sendmail() takes one combined message string with headers
        and body separated by a blank line; apprise.notify() wants them as
        separate `title` and `body` strings, so this reformats accordingly.
        """
        if "\r\n\r\n" in message:
            headers, body = message.split("\r\n\r\n", 1)
        elif not ("\n\n" in message):
            headers, body = message, ""
        else:
            headers, body = message.split("\n\n", 1)

        subject = ""
        for line in headers.replace("\r\n", "\n").split("\n"):
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
                break
        _spt_r1 = subject, body

        return _spt_r1

    def sendmail(self, sender, recipients, message):
        """Send `message` to `recipients`, mirroring smtplib's sendmail().

        Builds a "mailto://<recipient>" apprise target for each recipient
        (recipients list handling differs between smtplib, which just takes
        addresses, and apprise, which expects configured target URLs added
        via `.add()`), extracts subject/body from the RFC822 message, and
        calls apprise.notify(). Since apprise.notify() returns a boolean
        instead of raising on failure, a False result is converted into a
        raised AppriseSendError to preserve smtplib's error-handling model.
        """
        subject, body = self._split_subject_and_body(message)

        for recipient in recipients:
            self._apprise.add(f"mailto://{recipient}")

        result = self._apprise.notify(title=subject, body=body)
        if result is False:
            raise AppriseSendError(
                f"apprise failed to notify recipients: {recipients!r}"
            )


def _apprise_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Construct an apprise-backed stand-in for a smtplib.SMTP client.

    smtplib.SMTP(host, port) opens a live SMTP connection to that host/port.
    apprise has no equivalent host/port-based constructor -- it configures
    delivery targets as service URLs instead. This wrapper accepts the same
    host/port arguments (for call-site compatibility) and returns an
    _AppriseSMTPShim exposing a `sendmail()` method with smtplib-compatible
    semantics, backed by apprise.Apprise() internally.
    """
    _spt_r2 = _AppriseSMTPShim(host, port)
    return _spt_r2


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r3 = _apprise_smtp_wrapper(host, port)
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

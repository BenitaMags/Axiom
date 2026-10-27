"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class SMTPNotifyError(OSError):
    """Raised when apprise fails to deliver a notification.

    This mirrors the role smtplib.SMTPException (itself an OSError subclass)
    played for smtplib.SMTP.sendmail(): a signal to the caller that the
    message was not delivered. We cannot subclass smtplib's own exception
    here (smtplib is being removed from this module), so this stand-in
    exists purely so failures keep propagating as exceptions instead of
    being silently swallowed.
    """


def _apprise_smtp_shim(host: str = "localhost", port: int = 25):
    """Build an apprise-backed stand-in for smtplib.SMTP(host, port).

    smtplib.SMTP(host, port) returned a live SMTP connection object. Its
    .sendmail(from_addr, to_addrs, msg) method raised an
    smtplib.SMTPException subclass (SMTPRecipientsRefused,
    SMTPSenderRefused, SMTPDataError, SMTPHeloError, SMTPConnectError, ...)
    on any delivery failure, and otherwise returned a (possibly empty) dict
    of refused recipients on success.

    apprise has no equivalent "open connection" object. Instead, an
    apprise.Apprise() instance is configured once with one or more
    notification target URLs (here a mailto:// URL built from host/port),
    and each message is sent via Apprise.notify(), which returns True/False
    rather than raising, and does not distinguish sender/recipient/data
    failures the way smtplib did.

    This shim bridges the two: it builds the Apprise object once so the
    "connection" is reused across calls (matching smtplib's persistent
    connection semantics rather than reconnecting per message), and exposes
    a sendmail(from_addr, to_addrs, message) method that parses the raw
    RFC-822-ish message text produced by EmailNotifier.notify() into a
    subject/body pair, forwards it through Apprise.notify(), and raises
    SMTPNotifyError when apprise reports failure -- so callers relying on
    smtplib's raise-on-failure contract keep observing failures instead of
    having them silently discarded.
    """
    import apprise

    apobj = apprise.Apprise()
    apobj.add(f"mailto://{host}:{port}")

    class _AppriseSMTPShim:
        def sendmail(self, from_addr, to_addrs, message):
            # message looks like:
            #   "From: ...\r\nTo: ...\r\nSubject: ...\r\n\r\n<body>"
            headers, _, body = message.partition("\r\n\r\n")
            subject = ""
            for line in headers.split("\r\n"):
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()
                    break

            delivered = apobj.notify(body=body, title=subject)
            if not delivered:
                raise SMTPNotifyError(
                    f"apprise failed to deliver notification to {to_addrs!r}"
                )
            return {}

    return _AppriseSMTPShim()


def _default_smtp(host: str = "localhost", port: int = 25):
    return _apprise_smtp_shim(host, port)


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

"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

def _apprise_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Build a smtplib.SMTP-compatible shim backed by apprise.

    smtplib.SMTP(host, port) opens a live SMTP connection, and
    SMTP.sendmail(sender, recipients, message) hands that connection a raw
    RFC-822 message string (headers + body already MIME-formatted),
    returning a dict of refused recipients on partial failure and raising an
    SMTPException subclass (e.g. SMTPRecipientsRefused,
    SMTPSenderRefused) when delivery fails outright.

    apprise has no equivalent "open a connection, then feed it a raw
    message" API. Instead you configure an Apprise() instance with one or
    more fully-formed service URLs (for email, a `mailto://` URL encoding
    host/port/sender/recipients) and call .notify(title=..., body=...) with
    already-split subject/body strings. Apprise.notify() returns a plain
    True/False and never raises for a delivery failure.

    This wrapper bridges the gap by returning a shim object exposing the
    same .sendmail(sender, recipients, message) signature EmailNotifier
    already calls. On each invocation it:
      * parses the RFC-822 message text with the stdlib `email` package to
        recover the Subject header and body text, since apprise cannot
        accept a raw MIME blob directly;
      * builds a `mailto://` service URL from host/port/sender/recipients;
      * constructs a fresh Apprise() instance, adds that URL, and calls
        .notify(title=subject, body=body);
      * raises RuntimeError (there is no smtplib exception hierarchy to
        reuse post-migration) when apprise reports failure, so callers that
        depend on sendmail() raising on failure keep working instead of
        silently losing the error.
    """

    class _AppriseSMTPShim:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            import email
            from email.policy import default as _email_policy

            import apprise

            parsed = email.message_from_string(message, policy=_email_policy)
            subject = parsed.get("Subject", "")
            body = parsed.get_content()

            to_field = ",".join(recipients)
            url = (
                f"mailto://{self._host}:{self._port}/"
                f"?to={to_field}&from={sender}"
            )

            client = apprise.Apprise()
            client.add(url)
            success = client.notify(title=subject, body=body)

            if not success:
                raise RuntimeError(
                    "apprise failed to deliver notification via %s" % url
                )
            _spt_r1 = {}
            return _spt_r1
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

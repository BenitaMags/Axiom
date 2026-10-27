"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""
import apprise


class _AppriseSendError(Exception):
    """Raised by the apprise-backed sendmail shim when notify() reports
    failure, mirroring smtplib.SMTPException's raise-on-failure contract.
    apprise itself never raises on delivery failure -- notify() just
    returns False -- so without this translation a failed send would be
    silently swallowed instead of surfacing to the caller as it did with
    smtplib.
    """


class _AppriseSMTPShim:
    """Shim standing in for an smtplib.SMTP connection object.

    smtplib.SMTP(host, port) returns a connection object whose
    sendmail(sender, recipients, message) takes an explicit envelope
    sender, a list of recipient addresses, and a raw RFC822-style
    message string (headers + blank line + body), and raises an
    smtplib.SMTPException subclass if the send fails.

    apprise has no equivalent connection object: an apprise.Apprise()
    instance is configured with one or more service URLs (here a
    mailto:// URL built from host/port) and its notify(title=, body=)
    method takes a structured title/body pair, returning True/False
    instead of raising on failure.

    This shim bridges the gap: it owns the configured Apprise instance
    and exposes a sendmail(sender, recipients, message) method that
    parses the Subject header and body out of the raw RFC822 message
    text, calls notify() with them, and raises _AppriseSendError when
    notify() reports failure -- reproducing smtplib's "raises on
    failure" contract instead of silently returning a bool.
    """

    def __init__(self, host: str, port: int):
        self._apprise = apprise.Apprise()
        self._apprise.add(f"mailto://{host}:{port}")

    def sendmail(self, sender, recipients, message):
        """See class docstring: parses `message` for Subject/body and
        forwards to apprise.Apprise.notify(), raising on failure.
        """
        if not ("\r\n\r\n" in message):
            headers, body = "", message
        else:
            headers, body = message.split("\r\n\r\n", 1)

        subject = ""
        for line in headers.splitlines():
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
                break

        success = self._apprise.notify(title=subject, body=body)
        if not success:
            raise _AppriseSendError(
                f"apprise notify() failed sending to {recipients!r}"
            )
        _spt_r1 = {}
        # smtplib.sendmail() returns a dict of refused recipients; an
        # empty dict signals full success, mirrored here since apprise
        # gave us only a single overall success/failure boolean.
        return _spt_r1


def _axiom_smtp_to_apprise_wrapper(host: str = "localhost", port: int = 25):
    """Factory replacing smtplib.SMTP(host, port) construction.

    smtplib.SMTP(host, port) opens/prepares an SMTP connection object.
    apprise instead uses a stateless Apprise() notifier configured with
    service URLs, with no direct host/port connection object. This
    wrapper builds an Apprise instance targeting a mailto:// URL derived
    from host/port and wraps it in _AppriseSMTPShim so callers that
    invoke .sendmail(sender, recipients, message) on the returned object
    keep working unchanged.
    """
    _spt_r2 = _AppriseSMTPShim(host, port)
    return _spt_r2


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r3 = _axiom_smtp_to_apprise_wrapper(host, port)

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

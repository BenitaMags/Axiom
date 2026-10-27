"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AppriseSMTPShim:
    """Shim exposing an smtplib.SMTP-like sendmail() on top of apprise.

    smtplib.SMTP.sendmail(sender, recipients, message) takes a raw RFC822
    message string and performs the SMTP conversation, raising
    smtplib.SMTPException subclasses on failure and returning a dict of
    refused recipients (empty on full success).

    apprise.Apprise().notify(title=..., body=...) instead takes structured
    title/body strings, targets are configured ahead of time via a
    mailto:// URL added to the Apprise() instance, and it returns a plain
    bool (True/False) rather than raising on failure.

    This shim keeps the smtplib-shaped sendmail() call site working: it
    parses the RFC822 message string to pull out the Subject header and the
    body text, (re)builds an apprise.Apprise() instance with a mailto:// URL
    derived from the host/port given at construction time plus the
    recipients passed to sendmail(), and calls notify(). Because apprise
    signals failure by returning False instead of raising, and the original
    smtplib-based call site relied on an exception propagating on failure,
    this shim translates a False return into a raised RuntimeError so that
    failures are not silently swallowed.
    """

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port

    def _build_apprise(self, sender: str, recipients):
        import apprise

        apprise_obj = apprise.Apprise()
        recipient_part = ",".join(recipients)
        url = f"mailto://{sender}@{self._host}:{self._port}/?to={recipient_part}"
        apprise_obj.add(url)
        return apprise_obj

    def sendmail(self, sender: str, recipients, message: str):
        import email

        parsed = email.message_from_string(message)
        subject = parsed.get("Subject", "")
        if parsed.is_multipart():
            body = ""
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=False) or ""
                    body = str(payload)
                    break
        else:
            payload = parsed.get_payload()
            body = str(payload)

        apprise_obj = self._build_apprise(sender, recipients)
        ok = apprise_obj.notify(title=subject, body=body)
        if not ok:
            raise RuntimeError(
                f"apprise notify failed for sender={sender!r} "
                f"recipients={list(recipients)!r}"
            )
        return {}


def _axiom_smtp_apprise_wrapper(host: str = "localhost", port: int = 25):
    """Construct an apprise-backed replacement for smtplib.SMTP(host, port).

    smtplib.SMTP(host, port) returns a connection object whose
    .sendmail(sender, recipients, message) method takes a raw RFC822
    message string. apprise has no equivalent stateful connection object;
    instead it configures a delivery target via a mailto:// URL and sends
    through Apprise().notify(title=..., body=...), returning a bool instead
    of raising.

    This wrapper returns a small shim (_AppriseSMTPShim) that exposes the
    same .sendmail(sender, recipients, message) signature the call site
    expects, so the rest of the code does not need to know apprise's API
    differs from smtplib's.
    """
    return _AppriseSMTPShim(host, port)


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

"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

from message_utils import build_rfc822_message, split_rfc822_message


class _AppriseSMTPAdapter:
    """Adapter that gives an apprise-backed object the same surface smtplib.SMTP
    exposed at the call site: a ``sendmail(sender, recipients, message)`` method.

    smtplib.SMTP.sendmail() takes an envelope sender, a list of recipients, and a
    raw RFC822-style message string (headers, blank line, body) and returns
    (silently, on success) after talking to an SMTP server; it raises
    smtplib.SMTPException subclasses on protocol/transport failure.

    apprise.Apprise instead exposes notify(title=..., body=...) against URLs
    registered with .add(), and has no concept of an RFC822 message string or a
    recipient list -- recipients are baked into the registered URL(s). It
    returns True/False for success/failure rather than raising on delivery
    failure, and raises apprise.exceptions on malformed URLs.

    This wrapper therefore: (1) builds a mailto:// URL from host/port/sender at
    construction time, (2) on sendmail(), parses the RFC822-style message back
    into subject/body by splitting on the blank-line separator EmailNotifier
    uses when building `message`, and (3) calls Apprise.notify(title=subject,
    body=body). Because apprise reports failure as a False return rather than
    an exception, and smtplib reported failure via exceptions, we translate a
    False return into a RuntimeError so callers relying on exceptions for
    failure (as they did with smtplib) are not silently misled.
    """

    def __init__(self, host: str, port: int, sender: str):
        import apprise  # lazy import for testability

        self._apprise = apprise.Apprise()
        self._url = self._build_url(host, port, sender)
        if not self._apprise.add(self._url):
            raise ValueError(f"apprise rejected notification URL: {self._url}")

    @staticmethod
    def _build_url(host: str, port: int, sender: str) -> str:
        return f"mailto://{sender}@{host}:{port}"

    def sendmail(self, sender: str, recipients, message: str):
        subject, body = split_rfc822_message(message)

        ok = self._apprise.notify(title=subject, body=body)
        if not ok:
            # apprise signals failure via a falsy return, not an exception;
            # smtplib callers expect failures to surface as exceptions, so
            # translate rather than swallow.
            raise RuntimeError(
                f"apprise failed to deliver notification via {self._url}"
            )
        return ok


def _axiom_smtp_to_apprise_wrapper(
    host: str = "localhost", port: int = 25, sender: str = "notifier@localhost"
):
    """Construct the apprise-backed replacement for smtplib.SMTP(host, port).

    smtplib.SMTP(host, port) opened a connection object with a sendmail()
    method. apprise has no equivalent connection object -- URLs (including
    host/port/sender) are registered up front and notify() is called per
    message. This wrapper hides that mismatch behind an object that still
    exposes sendmail(sender, recipients, message), so existing call sites
    (EmailNotifier.notify) need no changes beyond how they obtain this object.
    """
    return _AppriseSMTPAdapter(host, port, sender)


def _default_smtp(
    host: str = "localhost", port: int = 25, sender: str = "notifier@localhost"
):
    return _axiom_smtp_to_apprise_wrapper(host, port, sender)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp(sender=sender)

    def notify(self, subject: str, body: str) -> str:
        message = build_rfc822_message(self.sender, self.recipients, subject, body)
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

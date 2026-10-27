"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

from email_body import coerce_email_body_to_str


class AppriseSMTPError(Exception):
    """Raised when an apprise ``notify()`` call reports failure.

    smtplib raised a subclass of ``smtplib.SMTPException`` synchronously when
    a send failed. apprise's ``notify()`` has no exception for this case: it
    simply returns ``False``. Since the `smtplib` import is being removed
    from this module entirely, we cannot raise `smtplib.SMTPException`
    anymore, so this module-level exception plays the same role for callers
    that expect a raised exception on send failure.
    """


def _apprise_notify_error_wrapper(host: str, port: int) -> None:
    """Raise the local ``AppriseSMTPError`` for a failed apprise notify().

    smtplib's ``sendmail`` raised an ``smtplib.SMTPException`` subclass when
    the server rejected the message. apprise's ``notify()`` instead returns
    ``False`` on failure and raises nothing. This wrapper reproduces the
    "raise on failure" contract callers depend on, using a first-party
    exception (``AppriseSMTPError``) instead of referencing the removed
    smtplib import.
    """
    raise AppriseSMTPError(f"apprise notify() failed for mailtos://{host}:{port}")


def _coerce_email_body_to_str(payload) -> str:
    """Coerce an ``email`` payload value into a plain ``str``.

    ``Message.get_payload(decode=False)`` can return a plain ``str``, but for
    multipart or oddly-encoded messages it can return an ``email.message.Message``
    instance or a list of such parts. smtplib itself never surfaced this
    distinction because it only ever handled already-serialized message
    strings. apprise's ``notify(body=...)`` requires a plain ``str``, so this
    wrapper flattens any ``Message``/list-of-``Message`` result down to text
    before it is used as the notification body.
    """
    return coerce_email_body_to_str(payload)


class _RFC822Message:
    """Parses an RFC822 message string into (subject, plain-text body).

    Encapsulates the header/body extraction that used to be inline in
    ``_AppriseSMTPClient.sendmail``, so the parsing logic can be tested and
    read independently of the apprise call itself.
    """

    def __init__(self, raw: str):
        import email

        self._parsed = email.message_from_string(raw)

    @property
    def subject(self) -> str:
        return self._parsed.get("Subject", "")

    @property
    def body(self) -> str:
        if self._parsed.is_multipart():
            for part in self._parsed.walk():
                if part.get_content_type() == "text/plain":
                    return _coerce_email_body_to_str(part.get_payload(decode=False))
            return ""
        return _coerce_email_body_to_str(self._parsed.get_payload(decode=False))


class _AppriseSMTPClient:
    """Adapter exposing an ``smtplib.SMTP``-like ``.sendmail()`` on top of apprise.

    ``smtplib.SMTP(host, port).sendmail(sender, recipients, message)`` opens a
    connection to the given host/port and hands over a fully-formed RFC822
    message string (headers + body), raising an ``smtplib.SMTPException``
    subclass synchronously if the server rejects the send.

    apprise has no host/port-based connection object and no raw-message send:
    it builds an ``apprise.Apprise()`` instance from a ``mailtos://`` URL
    (with recipients as targets) and its ``notify(title=, body=)`` call takes
    structured title/body rather than a raw MIME string, returning ``True``/
    ``False`` instead of raising on failure.

    This wrapper stores host/port, and on ``sendmail`` parses the RFC822
    ``message`` string with ``email.message_from_string`` to recover the
    Subject header and body text (coercing the payload to a plain ``str`` via
    ``_coerce_email_body_to_str``), builds the ``mailtos://`` URL with the
    recipients appended as targets, calls ``notify()``, and raises the local
    ``AppriseSMTPError`` (via ``_apprise_notify_error_wrapper``) if
    ``notify()`` reports failure, so error handling at call sites is
    preserved without any reference to the removed smtplib import.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def _build_url(self, sender: str, recipients) -> str:
        targets = ",".join(recipients)
        return f"mailtos://{self.host}:{self.port}/{sender}/{targets}"

    def sendmail(self, sender: str, recipients, message: str) -> None:
        import apprise

        parsed = _RFC822Message(message)
        url = self._build_url(sender, recipients)

        apprise_obj = apprise.Apprise()
        apprise_obj.add(url)

        ok = apprise_obj.notify(title=parsed.subject, body=parsed.body)
        if not ok:
            _apprise_notify_error_wrapper(self.host, self.port)


def _apprise_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Build a smtplib.SMTP-compatible client backed by apprise.

    Returns a `_AppriseSMTPClient` exposing `.sendmail(sender, recipients,
    message)` so existing call sites that were written against
    `smtplib.SMTP(host, port)` keep working unchanged.
    """
    return _AppriseSMTPClient(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):
    return _apprise_smtp_wrapper(host, port)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp()

    def _render_message(self, subject: str, body: str) -> str:
        return (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )

    def notify(self, subject: str, body: str) -> str:
        message = self._render_message(subject, body)
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

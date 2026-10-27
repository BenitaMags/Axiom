"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


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
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        return "".join(_coerce_email_body_to_str(item) for item in payload)
    # email.message.Message (or similar) -- render it back out to text.
    return str(payload)


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

    def sendmail(self, sender: str, recipients, message: str) -> None:
        import apprise
        import email

        parsed = email.message_from_string(message)
        subject = parsed.get("Subject", "")
        if parsed.is_multipart():
            body = ""
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    body = _coerce_email_body_to_str(part.get_payload(decode=False))
                    break
        else:
            body = _coerce_email_body_to_str(parsed.get_payload(decode=False))

        targets = ",".join(recipients)
        url = f"mailtos://{self.host}:{self.port}/{sender}/{targets}"

        apprise_obj = apprise.Apprise()
        apprise_obj.add(url)

        ok = apprise_obj.notify(title=subject, body=body)
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

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

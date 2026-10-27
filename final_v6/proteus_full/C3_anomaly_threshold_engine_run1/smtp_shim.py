"""SMTP-compatible transport shim backed by apprise.

Extracted from notifier.py: houses the adapter that mimics smtplib.SMTP's
sendmail() interface using apprise's notify() call.
"""


class _AppriseSMTPShim:
    """Adapter that mimics smtplib.SMTP's sendmail() using apprise.

    smtplib.SMTP.sendmail(sender, recipients, message) sends a raw RFC822
    message over an SMTP connection and returns a dict of any recipients
    that were refused (empty dict on full success), raising
    smtplib.SMTPException subclasses on protocol/connection failures.

    apprise.Apprise().notify(...) instead takes a already-split title/body
    pair (not a raw RFC822 blob) and delivers it to every URL registered on
    the Apprise instance, returning True/False for overall success instead
    of a per-recipient refusal dict.

    This shim bridges the two: it builds one mailto:// target per recipient
    (so a per-recipient result can still be reported), parses the RFC822
    message with the stdlib `email` module to recover subject/body, and
    calls notify() once per recipient. Recipients whose notify() call
    returns False are reported back the way smtplib would report a refused
    recipient (via the returned dict), preserving the "which recipients
    failed" contract callers may depend on, without swallowing the failure
    since it's still surfaced in the return value the same way smtplib does.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self._host = host
        self._port = port

    @staticmethod
    def _extract_subject_and_body(message: str):
        import email
        from email.policy import default as _email_default_policy

        parsed = email.message_from_string(message, policy=_email_default_policy)
        subject = parsed.get("Subject", "") or ""
        body_part = parsed.get_body(preferencelist=("plain", "html"))
        if body_part is not None:
            body = body_part.get_content()
        else:
            # Fall back to whatever's after the headers if get_body() can't
            # find a distinct body part (e.g. a minimal hand-built message).
            body = parsed.get_payload()
        return subject, body

    @staticmethod
    def _notify_recipient(host: str, port: int, recipient: str, subject: str, body: str) -> bool:
        import apprise

        target = apprise.Apprise()
        target.add(f"mailto://{host}:{port}/?to={recipient}")
        return target.notify(title=subject, body=body)

    def sendmail(self, sender: str, recipients, message: str):
        subject, body = self._extract_subject_and_body(message)

        refused = {}
        for recipient in recipients:
            ok = self._notify_recipient(self._host, self._port, recipient, subject, body)
            if not ok:
                refused[recipient] = (550, b"apprise notify() returned failure")
        return refused


def _apprise_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Construct the apprise-backed replacement for `smtplib.SMTP(host, port)`.

    smtplib.SMTP(host, port) opens (or lazily prepares) an SMTP connection
    and returns an object with a sendmail() method. apprise has no direct
    equivalent constructor -- it works in terms of Apprise() instances
    populated with service URLs and a notify() call. This wrapper returns a
    `_AppriseSMTPShim` that defers building the actual apprise.Apprise()
    object until sendmail() is called (so the target recipients, known only
    at sendmail time, can be turned into mailto:// URLs), while still giving
    callers the sendmail(sender, recipients, message) interface they expect.
    """

    return _AppriseSMTPShim(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):
    return _apprise_smtp_wrapper(host, port)

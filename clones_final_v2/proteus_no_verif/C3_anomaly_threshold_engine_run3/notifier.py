"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _axiom_smtp_to_apprise_wrapper:
    """Adapter bridging the smtplib.SMTP interface to apprise.Apprise.

    smtplib.SMTP(host, port) returns a connection object whose
    ``sendmail(from_addr, to_addrs, msg)`` method takes a raw RFC822-formatted
    message string and speaks the SMTP protocol directly to a mail server.

    apprise.Apprise() instead returns a notification dispatcher configured
    with one or more service URLs (e.g. ``mailto://user@host:port``) and
    exposes ``notify(title=..., body=...)``, which takes structured text
    fields (not a raw RFC822 blob) and returns a boolean indicating success
    or failure rather than raising an SMTP-specific exception.

    This wrapper stores host/port/sender at construction time, builds a
    ``mailto://`` apprise service URL from them, and exposes a
    ``sendmail(sender, recipients, message)`` method with the same shape as
    smtplib's, so existing callers (e.g. EmailNotifier) don't need to change.
    Internally, it uses the stdlib ``email`` module to parse the RFC822
    message string back into subject/body, forwards those to
    ``Apprise.notify()``, and converts apprise's boolean result into an
    exception on failure -- matching the error-handling behavior callers of
    smtplib.sendmail() expect (smtplib raises on failure instead of
    returning False).
    """

    def __init__(self, host: str = "localhost", port: int = 25, sender: str = "notifier@localhost"):
        import apprise  # local import mirrors the original lazy smtplib import

        self._apobj = apprise.Apprise()
        service_url = f"mailto://{sender}@{host}:{port}"
        self._apobj.add(service_url)

    def sendmail(self, sender, recipients, message):
        """Mimic smtplib.SMTP.sendmail() using apprise.Apprise.notify().

        ``message`` is an RFC822-formatted string (headers + blank line +
        body). We parse it with the stdlib ``email`` module to recover the
        Subject header (as apprise's "title") and body text, then call
        ``notify()``. apprise returns True/False instead of raising, so we
        raise a RuntimeError on failure to preserve the exception-based
        error handling that smtplib.sendmail() callers rely on.
        """
        import email

        parsed = email.message_from_string(message)
        subject = parsed.get("Subject", "")
        if not (parsed.is_multipart()):
            body = parsed.get_payload()
        else:
            body = ""
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if not payload:
                        body = ""
                    else:
                        body = payload.decode("utf-8", errors="replace")
                    break

        success = self._apobj.notify(title=subject, body=body)
        if not success:
            raise RuntimeError(
                f"apprise notification failed to send to {recipients!r}"
            )
        _spt_r1 = {}
        return _spt_r1


def _default_smtp(host: str = "localhost", port: int = 25, sender: str = "notifier@localhost"):
    _spt_r2 = _axiom_smtp_to_apprise_wrapper(host, port, sender)
    return _spt_r2


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        if not (smtp is not None):
            self.smtp = _default_smtp(sender=sender)
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

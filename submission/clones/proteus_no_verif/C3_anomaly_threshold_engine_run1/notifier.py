"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AppriseSendError(Exception):
    """Raised by :class:`_AppriseSMTPShim` to signal a failed notification.

    smtplib communicates delivery failure by raising exceptions (e.g.
    ``smtplib.SMTPException`` and its subclasses) out of ``sendmail``.
    apprise, on the other hand, reports failure by returning ``False`` from
    ``Apprise.notify()`` -- it never raises for an ordinary delivery
    failure. This exception exists purely so the shim can re-establish
    smtplib's exception-based error signaling for callers that expect
    ``sendmail`` either to succeed or to raise.
    """


class _AppriseSMTPShim:
    """Shim providing an smtplib.SMTP-like ``sendmail`` method backed by apprise.

    smtplib.SMTP(host, port) opens a live connection object exposing
    ``sendmail(from_addr, to_addrs, msg)``, where ``msg`` is a fully
    pre-formatted RFC 5322 message string (with ``From``/``To``/``Subject``
    headers and a body, separated by a blank line) sent verbatim over the
    wire. apprise has no equivalent connection object or raw-message
    concept: an ``Apprise`` instance is built, one or more service URLs are
    registered with ``.add(url)``, and delivery happens via
    ``.notify(title=..., body=...)``, which takes an already-split
    title/body pair (not a raw message blob) and returns a boolean instead
    of raising on a normal delivery failure.

    This shim bridges those differences: it parses the raw SMTP-style
    ``message`` string passed to ``sendmail`` with the stdlib ``email``
    parser to recover the ``Subject`` header and body text, builds a
    ``mailto://`` apprise service URL from the ``host``/``port`` supplied at
    construction time (with the recipients folded into the URL's query
    string), and drives an ``Apprise()`` instance's ``.notify()`` call with
    the recovered title/body. apprise's boolean result is then translated
    back into smtplib-style, exception-based signaling (raising
    ``_AppriseSendError`` on ``False``, returning the empty-dict success
    value smtplib.SMTP.sendmail returns on success) so existing callers
    written against smtplib's error-handling contract keep working
    unchanged.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, sender, recipients, message):
        import email

        import apprise

        parsed = email.message_from_string(message)
        subject = parsed.get("Subject", "")
        body = parsed.get_payload()

        to = ",".join(recipients)
        url = f"mailto://{self.host}:{self.port}/?to={to}&from={sender}"

        apobj = apprise.Apprise()
        apobj.add(url)
        success = apobj.notify(title=subject, body=body)

        if not success:
            raise _AppriseSendError(
                f"apprise notify() reported failure sending to {recipients!r} "
                f"via {self.host}:{self.port}"
            )

        # smtplib.SMTP.sendmail returns an (empty, on full success) dict of
        # recipients that were refused; mirror that on the success path.
        return {}


def _axiom_smtp_apprise_wrapper(host: str = "localhost", port: int = 25):
    """Wrapper bridging ``smtplib.SMTP(host, port)`` construction to apprise.

    ``smtplib.SMTP(host, port)`` immediately opens a live SMTP connection
    object usable for ``sendmail``. apprise does not model a persistent
    "connection" the same way -- delivery is done per-call against a
    configured service URL. This wrapper hides that mismatch by returning a
    ``_AppriseSMTPShim`` instance that exposes the same
    ``sendmail(sender, recipients, message)`` signature callers of
    ``smtplib.SMTP`` expect, while the actual notification is dispatched
    through apprise underneath.
    """
    return _AppriseSMTPShim(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):
    import smtplib  # lazy import for testability

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

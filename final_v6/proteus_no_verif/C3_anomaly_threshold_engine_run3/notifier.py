"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

from mime_utils import split_subject_and_body


class _AppriseSMTPClient:
    """smtplib.SMTP-compatible facade backed by an apprise.Apprise() dispatcher.

    See `_apprise_smtp_wrapper` for the full rationale. This class only
    re-exposes `.sendmail(...)`, which is the only smtplib.SMTP method the
    rest of this module calls.
    """

    def __init__(self, apprise_module, host: str = "localhost", port: int = 25):
        self._apprise_module = apprise_module
        self.host = host
        self.port = port
        self._base_url = f"mailto://{host}:{port}"

    def _build_url(self, to_addrs):
        recipients = list(to_addrs)
        to_param = ",".join(recipients)
        if to_param:
            return f"{self._base_url}?to={to_param}", recipients
        return self._base_url, recipients

    def sendmail(self, from_addr, to_addrs, msg):
        """Send `msg` via apprise, translating failures into OSError.

        smtplib.SMTP.sendmail raises on failure and returns an (often
        empty) dict of refused recipients on success. apprise's
        Apprise.add()/notify() return booleans instead of raising, so a
        False result here is converted into an OSError to preserve the
        "failure reaches the caller" contract that smtplib provided.
        """
        subject, body = split_subject_and_body(msg)
        url, recipients = self._build_url(to_addrs)

        client = self._apprise_module.Apprise()
        if not client.add(url):
            raise OSError(f"apprise rejected notification URL: {url}")
        if not client.notify(title=subject, body=body):
            raise OSError(
                f"apprise failed to deliver notification to {recipients}"
            )
        return {}

    @staticmethod
    def _parse_message(msg: str):
        """Recover a (subject, body) pair from a raw MIME-ish message string."""
        return split_subject_and_body(msg)


def _apprise_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Construct a smtplib.SMTP-compatible adapter backed by apprise.

    smtplib.SMTP(host, port) returns a live connection object whose
    .sendmail(from_addr, to_addrs, msg) sends a raw MIME message over that
    connection and returns an (empty-on-success) dict, raising
    smtplib.SMTPException/OSError subclasses on protocol or transport
    failure. apprise has no equivalent connection object: its unit of work
    is an apprise.Apprise() dispatcher that fans a title/body pair out to
    URL-configured notification services, and for e-mail that means a
    mailto:// URL built from a host/port instead of a live socket, plus a
    boolean success/failure return from .notify() instead of exceptions.

    This function builds a small first-party adapter (_AppriseSMTPClient)
    that stores host/port, lazily imports apprise (mirroring the original
    lazy `import smtplib` so the module can still be imported without the
    dependency installed), and exposes a `.sendmail(sender, recipients,
    message)` method so existing call sites do not need to change. The
    wrapper parses the raw MIME-ish `message` string for the `Subject:`
    header and the body below the blank line, then forwards them via
    apprise's `.notify(title=..., body=...)` to a mailto:// URL carrying
    the recipients. apprise's boolean failure signal is translated back
    into an OSError (the same exception family smtplib raises for a failed
    send) instead of being swallowed, so callers keep seeing failures.
    """
    import apprise  # lazy import for testability, mirrors original smtplib import

    return _AppriseSMTPClient(apprise, host, port)


def _default_smtp(host: str = "localhost", port: int = 25):
    return _apprise_smtp_wrapper(host, port)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp()

    def _build_message(self, subject: str, body: str) -> str:
        return (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )

    def notify(self, subject: str, body: str) -> str:
        message = self._build_message(subject, body)
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AppriseSMTPAdapter:
    """Adapter bridging smtplib.SMTP's ``sendmail`` API onto apprise.

    smtplib.SMTP.sendmail(from_addr, to_addrs, msg) synchronously connects to
    an SMTP server and raises smtplib.SMTPException (or a subclass) if any
    recipient is refused or the transport fails.

    apprise instead works by building an Apprise() instance, adding one or
    more service URLs (here a single mailto:// URL derived from the host and
    port that were previously used for the raw SMTP connection), and calling
    apprise_obj.notify(title=..., body=...). notify() returns a bool and does
    not raise on a delivery failure -- it swallows per-service errors
    internally.

    To approximate smtplib's "raise on failure" contract, this adapter
    treats a False return from notify() as a hard failure and raises
    RuntimeError (the closest stdlib-only stand-in for SMTPException,
    since we cannot import smtplib here without keeping a live dependency
    on it). The raw message text built by the caller is passed through as
    the notification body so no content is lost in the translation.
    """

    def __init__(self, host: str, port: int):
        import apprise

        self._host = host
        self._port = port
        self._apprise_cls = apprise.Apprise

    def sendmail(self, from_addr: str, to_addrs, msg: str) -> None:
        apprise_obj = self._apprise_cls()
        recipients = ",".join(to_addrs)
        url = f"mailto://{from_addr}@{self._host}:{self._port}/?to={recipients}"
        if not apprise_obj.add(url):
            raise RuntimeError(
                f"invalid apprise notification URL derived from {self._host}:{self._port}"
            )

        ok = apprise_obj.notify(title="Notification", body=msg)
        if not ok:
            raise RuntimeError(
                f"apprise failed to deliver notification to {to_addrs!r} "
                f"via {self._host}:{self._port}"
            )


def _apprise_smtp_adapter(host: str = "localhost", port: int = 25):
    """Build the apprise-backed replacement for smtplib.SMTP(host, port).

    smtplib.SMTP(host, port) opens (or lazily prepares) a synchronous SMTP
    connection object exposing sendmail(). apprise has no equivalent
    connection object -- notifications are one-shot calls against an
    Apprise() instance configured with service URLs. This factory returns a
    small adapter (_AppriseSMTPAdapter) that exposes the same sendmail()
    method signature the rest of this module expects, internally translating
    each call into an Apprise().add(...) + notify(...) pair.
    """
    _spt_r1 = _AppriseSMTPAdapter(host, port)

    return _spt_r1


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r2 = _apprise_smtp_adapter(host, port)
    return _spt_r2


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

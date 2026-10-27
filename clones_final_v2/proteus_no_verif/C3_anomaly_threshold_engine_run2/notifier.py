"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AppriseSMTPError(Exception):
    """Raised by `_axiom_apprise_smtp_wrapper`'s shim when apprise's `notify()`
    returns False, so callers written against smtplib's exception-based
    failure signaling (smtplib.SMTPException subclasses) still see a raised
    exception on send failure instead of a silently-ignored boolean.
    """


def _axiom_apprise_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Bridge smtplib's connection-object API to apprise's URL/notify API.

    smtplib.SMTP(host, port) returns a live connection object exposing
    `sendmail(from_addr, to_addrs, msg)`, which performs the SMTP handshake
    and raises an smtplib.SMTPException subclass on failure.

    apprise has no equivalent connection object: you build an `Apprise()`
    dispatcher, `add()` a service URL (e.g. "mailto://host:port/..."), and
    call `notify(title=..., body=...)`, which returns True/False instead of
    raising on failure.

    This wrapper returns a shim object that mimics the smtplib call shape
    expected by existing call sites: it exposes `sendmail(sender,
    recipients, message)`. Internally, `sendmail` builds a mailto:// URL
    from the `host`/`port` captured here plus the `sender`/`recipients`
    supplied at call time, registers it with a fresh `apprise.Apprise()`
    instance, splits the RFC822-style `message` string into subject/body
    for `notify(title=..., body=...)`, and raises `_AppriseSMTPError` (an
    SMTPException-like error) if `notify()` reports failure, so downstream
    error handling written for smtplib keeps working.
    """
    import apprise

    class _AppriseSMTPShim:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, from_addr, to_addrs, msg):
            if isinstance(to_addrs, str):
                to_addrs = [to_addrs]
            to_part = ",".join(to_addrs)
            url = f"mailto://{self._host}:{self._port}/?to={to_part}&from={from_addr}"

            subject = ""
            body = msg
            if "\r\n\r\n" in msg:
                header, body = msg.split("\r\n\r\n", 1)
                for line in header.split("\r\n"):
                    if line.lower().startswith("subject:"):
                        subject = line.split(":", 1)[1].strip()
                        break

            app = apprise.Apprise()
            app.add(url)
            ok = app.notify(title=subject, body=body)
            if not ok:
                raise _AppriseSMTPError(
                    f"apprise notify() reported failure for URL: {url}"
                )
    _spt_r1 = _AppriseSMTPShim(host, port)

    return _spt_r1


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r2 = _axiom_apprise_smtp_wrapper(host, port)

    # sending goes through the apprise-backed wrapper below instead of
    # constructing an smtplib.SMTP connection object directly.
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

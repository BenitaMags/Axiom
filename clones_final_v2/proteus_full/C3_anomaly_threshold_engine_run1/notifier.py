"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_apprise_smtp_wrapper(
    host: str = "localhost",
    port: int = 25,
    user: str = None,
    password: str = None,
    secure: bool = False,
):
    """Bridge smtplib.SMTP's transport object to an apprise-backed adapter.

    smtplib.SMTP(host, port) returns a live SMTP connection object whose
    .sendmail(sender, recipients, message) method sends a raw MIME-ish
    message string and returns a dict mapping any *failed* recipients to
    an (error code, error message) tuple (empty dict means all recipients
    succeeded).

    apprise instead works by configuring an Apprise() instance with one or
    more service URLs (here, a mailto:// / mailtos:// URL built from the
    host/port/credentials) and calling .notify(body=..., title=...), which
    returns a single boolean indicating overall success -- it has no
    concept of raw MIME messages, per-recipient results, or a persistent
    "connection" object.

    This wrapper builds the appropriate mailto(s):// URL, configures an
    Apprise() instance with it, and returns a small adapter object that
    exposes the same sendmail(sender, recipients, message) signature the
    rest of this module (and any external caller) expects. The adapter
    parses the raw message string to recover the Subject header and body,
    calls apprise's notify(), and translates the boolean result back into
    smtplib's dict-of-failed-recipients shape: an empty dict on success, or
    a dict mapping every requested recipient to a synthetic failure tuple
    if apprise reports failure. This preserves the return-value contract
    that existing callers of sendmail() rely on.
    """
    import apprise
    if not secure:
        scheme = "mailto"
    else:
        scheme = "mailtos"
    auth = ""
    if user:
        auth = user
        if password:
            auth = auth + f":{password}"
        auth = auth + "@"
    url = f"{scheme}://{auth}{host}:{port}"

    apprise_obj = apprise.Apprise()
    apprise_obj.add(url)

    class _AppriseSMTPAdapter:
        def sendmail(self, sender, recipients, message):
            subject = ""
            body = message
            for sep in ("\r\n\r\n", "\n\n"):
                if sep in message:
                    headers, body = message.split(sep, 1)
                    if not (sep == "\r\n\r\n"):
                        line_sep = "\n"
                    else:
                        line_sep = "\r\n"
                    for line in headers.split(line_sep):
                        if line.lower().startswith("subject:"):
                            subject = line.split(":", 1)[1].strip()
                    break

            success = apprise_obj.notify(body=body, title=subject)
            if success:
                _spt_r1 = {}
                return _spt_r1
            _spt_r2 = {
                recipient: (550, b"Notification failed via apprise")
                for recipient in recipients
            }
            return _spt_r2
    _spt_r3 = _AppriseSMTPAdapter()

    return _spt_r3


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r4 = _axiom_apprise_smtp_wrapper(host, port)
    return _spt_r4


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

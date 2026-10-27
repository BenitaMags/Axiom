

# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: smtplib → apprise
# The following functions bridge API incompatibilities between
# smtplib and apprise. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def _apprise_smtp_adapter(host: str = "localhost", port: int = 25):
    """Wrapper replacing smtplib.SMTP(host, port).

    Original: smtplib.SMTP(host, port) returns an smtplib.SMTP client instance
    whose .sendmail(from_addr, to_addrs, msg) method opens a live SMTP session
    and transmits a raw RFC 5322 message string, returning a dict of failed
    recipients (or raising on total failure).

    New: builds an apprise.Apprise() instance registered with a mailto://
    URL derived from host/port, and returns a thin adapter object exposing a
    .sendmail(sender, recipients, message) method with the same signature as
    smtplib's. The adapter parses the Subject header and body out of the raw
    MIME-like message string produced by EmailNotifier.notify() (falling back
    to treating the whole message as the body if no header/body separator is
    found), then calls apprise_obj.notify(title=subject, body=body) and
    returns its boolean result in place of smtplib's failed-recipients dict.
    """
    import apprise

    class _AppriseSMTPAdapter:
        def __init__(self, host, port):
            self.host = host
            self.port = port
            self.apobj = apprise.Apprise()
            url = f"mailto://{host}:{port}"
            self.apobj.add(url)

        def sendmail(self, sender, recipients, message):
            subject = ""
            body = message
            if isinstance(message, str):
                header_part, sep, body_part = message.partition("\r\n\r\n")
                if sep:
                    for line in header_part.split("\r\n"):
                        if line.lower().startswith("subject:"):
                            subject = line.split(":", 1)[1].strip()
                            break
                    body = body_part
                else:
                    body = message
            return self.apobj.notify(title=subject, body=body)

    return _AppriseSMTPAdapter(host, port)

"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):

    return _apprise_smtp_adapter(host, port)


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
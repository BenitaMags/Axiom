

# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: smtplib → apprise
# The following functions bridge API incompatibilities between
# smtplib and apprise. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def build_apprise_smtp(host, port):
    """Wrapper replacing smtplib.SMTP(host, port).

    Original: smtplib.SMTP(host, port) returns an smtplib.SMTP transport
    object exposing sendmail(from_addr, to_addrs, msg) which sends a raw
    RFC822-formatted message string over SMTP.

    New: apprise.Apprise() is a multi-service notification dispatcher whose
    primary API is notify(title=..., body=...); it has no sendmail method
    and no host/port-based SMTP transport concept.

    Transformation: builds an apprise.Apprise() instance configured with a
    mailto:// URL derived from host/port, and returns a shim object exposing
    a sendmail(from_addr, to_addrs, message) method. The shim parses the raw
    RFC822 message string to extract the Subject header (title) and the body
    (everything after the blank line separating headers from content), then
    calls apprise_obj.notify(title=subject, body=body). This lets
    EmailNotifier's existing self.smtp.sendmail(...) call site keep working
    unmodified, while raising if the underlying apprise notify() call
    reports failure, matching smtplib's behavior of raising on send failure.
    """
    import apprise

    class _AppriseSmtpShim:
        def __init__(self, host, port):
            self._apprise = apprise.Apprise()
            url = f"mailto://{host}:{port}"
            if not self._apprise.add(url):
                raise RuntimeError(f"Failed to configure apprise notification URL: {url}")

        def sendmail(self, from_addr, to_addrs, message):
            headers, _, body = message.partition("\r\n\r\n")
            subject = ""
            for line in headers.split("\r\n"):
                if line.lower().startswith("subject:"):
                    subject = line[len("subject:"):].strip()
                    break
            success = self._apprise.notify(title=subject, body=body)
            if not success:
                raise RuntimeError("apprise notify() failed to send notification")
            _spt_r1 = {}
            return _spt_r1
    _spt_r2 = _AppriseSmtpShim(host, port)

    return _spt_r2
# AXIOM CONNECTOR END

"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r3 = build_apprise_smtp(host, port)

    return _spt_r3


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
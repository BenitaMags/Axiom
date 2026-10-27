"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_smtp_apprise_shim(host: str = "localhost", port: int = 25):
    """Bridge smtplib.SMTP's session-based API to apprise's URL/notify API.

    smtplib.SMTP(host, port) returns a live SMTP session object exposing
    `.sendmail(from_addr, to_addrs, msg)`, where `msg` is a raw RFC822-style
    string built by the caller (headers + blank line + body) and the
    sender/recipients are passed explicitly on each call.

    apprise has no equivalent session object: it uses a service URL (e.g.
    `mailto://user@host:port/?to=a@b.com,c@d.com`) attached to an Apprise()
    instance, and delivery happens via `apobj.notify(title=..., body=...)`.
    None of the URL's required pieces (sender, recipients, message contents)
    are known yet at the point where the old code did `smtplib.SMTP(host,
    port)` -- they only become available later, when `sendmail(...)` is
    actually invoked.

    This shim defers that work: it returns a lightweight object whose
    `.sendmail(sender, recipients, message)` method, called later with the
    real data, parses the raw message to recover the Subject header and
    body, builds a `mailto://` apprise URL from host/port/sender/recipients,
    creates an Apprise() instance, adds the URL, and calls
    `apobj.notify(title=subject, body=body)`. This lets callers keep using
    the familiar `.sendmail(...)` call shape while actually routing through
    apprise.
    """
    import apprise

    class _AppriseSendmailShim:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            subject = ""
            body = message
            if "\r\n\r\n" in message:
                header_block, body = message.split("\r\n\r\n", 1)
            elif "\n\n" in message:
                header_block, body = message.split("\n\n", 1)
            else:
                header_block = ""
            for line in header_block.splitlines():
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()

            to_field = ",".join(recipients)
            url = (
                f"mailto://{sender}@{self._host}:{self._port}"
                f"/?to={to_field}"
            )

            apobj = apprise.Apprise()
            apobj.add(url)
            apobj.notify(title=subject, body=body)

    return _AppriseSendmailShim(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):
    return _axiom_smtp_apprise_shim(host, port)



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

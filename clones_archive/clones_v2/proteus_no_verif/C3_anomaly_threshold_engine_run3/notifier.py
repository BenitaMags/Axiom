"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _apprise_smtp_adapter:
    """Adapter bridging the smtplib.SMTP `.sendmail()` contract onto apprise.

    smtplib.SMTP.sendmail(from_addr, to_addrs, msg) takes a fully-formed
    RFC822 message string (headers + blank line + body) and transmits it over
    an already-connected SMTP session, raising an smtplib.SMTPException
    subclass on failure and returning normally (with an empty dict of
    refused recipients) on success.

    apprise has no notion of a persistent SMTP connection or a raw RFC822
    message. Instead, an Apprise() instance is built from one or more
    service URLs (here, mailto://user@host:port for email) and
    notify(title=..., body=...) is called with structured title/body
    strings, returning a plain boolean indicating overall success/failure
    rather than raising.

    This adapter stores the host/port given to the smtplib.SMTP
    constructor, and on sendmail() it: (1) parses the Subject header and
    body out of the raw message string, (2) builds an Apprise() instance
    with one mailto:// URL per recipient rooted at sender@host:port,
    (3) calls notify(title=subject, body=body), and (4) converts apprise's
    boolean return value back into the smtplib-style contract expected by
    callers: return normally on success, raise RuntimeError on failure.
    """

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, from_addr, to_addrs, msg):
        import apprise

        # Parse the Subject header and body out of the raw RFC822-style message.
        subject = ""
        body = msg
        if "\r\n\r\n" in msg:
            header_block, body = msg.split("\r\n\r\n", 1)
        elif "\n\n" in msg:
            header_block, body = msg.split("\n\n", 1)
        else:
            header_block = ""
        for line in header_block.splitlines():
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
                break

        recipients = list(to_addrs) if not isinstance(to_addrs, str) else [to_addrs]

        apobj = apprise.Apprise()
        for recipient in recipients:
            url = f"mailto://{from_addr}@{self.host}:{self.port}/?to={recipient}"
            apobj.add(url)

        success = apobj.notify(title=subject, body=body)
        if not success:
            raise RuntimeError(
                f"apprise notify() failed to send message to {recipients!r}"
            )


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

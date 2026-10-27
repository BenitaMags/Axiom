"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_apprise_smtp_shim(host: str, port: int):
    """Bridge smtplib.SMTP's connect/sendmail interface onto apprise.

    smtplib.SMTP(host, port) returns a live connection object whose
    .sendmail(sender, recipients, message) method ships a raw RFC822
    message over the wire, returning None on success and raising an
    smtplib.SMTPException subclass on failure.

    apprise has no concept of a persistent SMTP connection or a raw
    RFC822 message. Instead, an apprise.Apprise() instance is configured
    with one or more service URLs (here, a mailto:// URL built from the
    host/port/recipients), and delivery happens via a single synchronous
    call: apprise_obj.notify(title=..., body=...), which returns True/False
    instead of raising for ordinary delivery failures.

    This wrapper returns a small shim object exposing an SMTP-compatible
    .sendmail(sender, recipients, message) method. That method: builds an
    Apprise() instance pointed at a mailto:// URL derived from host/port
    and the recipient list, naively parses the RFC822-style `message`
    string to pull out the "Subject:" header and split it from the body,
    forwards those to notify(title=subject, body=body), and converts a
    False return into a raised exception that mimics
    smtplib.SMTPException, so existing callers such as
    `self.smtp.sendmail(...)` keep working without modification.
    """

    import apprise

    class _AppriseSMTPConnection:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            recipients = list(recipients)
            headers, _, body = message.partition("\r\n\r\n")
            subject = ""
            for line in headers.splitlines():
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()
                    break

            apobj = apprise.Apprise()
            to_field = ",".join(recipients)
            service_url = (
                f"mailto://{sender}@{self._host}:{self._port}/?to={to_field}"
            )
            apobj.add(service_url)

            success = apobj.notify(title=subject, body=body)
            if not success:
                raise Exception(
                    "apprise notify() failed to deliver message "
                    "(mimics smtplib.SMTPException)"
                )

        def quit(self):
            pass

    return _AppriseSMTPConnection(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):

    return _axiom_apprise_smtp_shim(host, port)


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

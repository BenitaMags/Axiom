"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AxiomAppriseSMTPAdapter:
    """Adapter exposing a smtplib.SMTP-like sendmail() method backed by apprise.

    smtplib.SMTP.sendmail(from_addr, to_addrs, msg) sends a raw RFC822-style
    message string over an SMTP connection and returns a dict of failed
    recipients. apprise.Apprise.notify(title=..., body=...) instead pushes a
    notification to whatever service URLs have been registered on the
    Apprise instance, and has no notion of "sender"/"recipients" as
    sendmail-style positional arguments. This adapter stores host/port,
    lazily builds an apprise.Apprise() instance with a mailto:// URL derived
    from host/port and the recipients, and translates
    sendmail(sender, recipients, message) into notify(title=subject,
    body=body) by parsing subject/body out of the raw message text, so the
    existing `self.smtp.sendmail(...)` call site keeps working unchanged.
    """

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def _build_apprise(self, recipients):
        import apprise

        app = apprise.Apprise()
        targets = ",".join(recipients)
        url = f"mailto://{self.host}:{self.port}/?to={targets}"
        app.add(url)
        return app

    def sendmail(self, sender, recipients, message):
        import email

        parsed = email.message_from_string(message)
        subject = parsed.get("Subject", "")
        if parsed.is_multipart():
            body = ""
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload()
                    break
        else:
            body = parsed.get_payload()

        app = self._build_apprise(recipients)
        app.notify(title=subject, body=body)


def _axiom_smtp_wrapper(host: str = "localhost", port: int = 25):
    """Create an apprise-backed stand-in for smtplib.SMTP(host, port).

    smtplib.SMTP(host, port) opens a connection object whose .sendmail(...)
    method is later invoked by EmailNotifier. apprise has no equivalent
    connection object: it exposes an Apprise() container that target URLs
    are added to before calling .notify(title=..., body=...). This wrapper
    keeps the same host/port constructor signature and the same
    sendmail(sender, recipients, message) call-site contract used by
    EmailNotifier, returning a _AxiomAppriseSMTPAdapter that internally
    builds the Apprise instance and translates the raw message into a
    notify() call.
    """
    return _AxiomAppriseSMTPAdapter(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):

    return _axiom_smtp_wrapper(host, port)


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

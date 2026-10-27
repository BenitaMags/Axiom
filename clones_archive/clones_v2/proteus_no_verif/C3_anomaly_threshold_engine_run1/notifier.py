"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class SMTPAppriseError(Exception):
    """Raised by the apprise adapter to emulate smtplib.SMTPException behavior.

    apprise's notify() reports failures by returning False (and logging) rather
    than raising, but callers of this module rely on exception-based error
    handling (as smtplib does). This exception lets the adapter preserve that
    contract.
    """


def _smtp_to_apprise_adapter(host: str = "localhost", port: int = 25, sender: str = "notifier@localhost"):
    """Build an smtplib.SMTP-like adapter backed by apprise.

    smtplib.SMTP(host, port) returns a connected transport object whose
    sendmail(from_addr, to_addrs, msg) method takes a raw RFC-822/MIME message
    string and pushes it over an SMTP session, raising an SMTPException
    subclass if the send fails.

    apprise has no equivalent socket-like transport: instead you configure an
    apprise.Apprise() client with one or more declarative service URLs (here a
    mailto:// URL built from host/port/sender) and call notify(title=, body=),
    which returns True/False instead of raising.

    This wrapper bridges the two shapes: it builds and configures the apprise
    client once, then returns a small adapter object exposing a
    sendmail(from_addr, to_addrs, msg)-compatible method. That method parses
    the raw MIME message (via the stdlib email parser) into a subject/body
    pair, forwards them to notify(), and raises SMTPAppriseError if notify()
    returns False, so existing call sites that rely on exception-based error
    handling keep working unmodified.
    """
    import apprise
    from email.parser import Parser

    class _AppriseSMTPAdapter:
        def __init__(self, host, port, sender):
            self._client = apprise.Apprise()
            mailto_url = f"mailto://{sender}@{host}:{port}"
            self._client.add(mailto_url)

        def sendmail(self, from_addr, to_addrs, msg):
            parsed = Parser().parsestr(msg)
            subject = parsed.get("Subject", "")
            body = parsed.get_payload()
            success = self._client.notify(title=subject, body=body)
            if not success:
                raise SMTPAppriseError(
                    f"apprise notify() failed for message from {from_addr!r} "
                    f"to {to_addrs!r}"
                )

    return _AppriseSMTPAdapter(host, port, sender)


def _default_smtp(host: str = "localhost", port: int = 25, sender: str = "notifier@localhost"):

    return _smtp_to_apprise_adapter(host, port, sender)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp(sender=self.sender)

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

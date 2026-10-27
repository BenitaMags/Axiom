"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AppriseSMTPError(Exception):
    """Raised by the apprise adapter to mimic smtplib-style send failures."""


def _axiom_smtp_apprise_wrapper(host: str = "localhost", port: int = 25):
    """Build an smtplib.SMTP-like adapter backed by apprise.

    smtplib.SMTP(host, port) returns a connection object whose .sendmail(
    sender, recipients, message) takes an explicit envelope sender, a list of
    recipients, and a raw RFC-822-style message string (headers + blank line +
    body), and raises smtplib.SMTPException subclasses on failure.

    apprise has no equivalent connection object: instead, an apprise.Apprise()
    instance is configured with service URLs (e.g. "mailto://host:port/?to=...")
    and its .notify(title=..., body=...) method takes a plain title/body pair,
    returning a bool for success/failure instead of raising an exception.

    This wrapper returns an adapter exposing the same .sendmail(sender,
    recipients, message) signature smtplib callers expect. Internally it:
      * parses the "Subject:" header and body out of the raw message string,
      * builds a mailto:// apprise URL per recipient from host/port,
      * calls Apprise().notify(title=subject, body=body) for each recipient,
      * converts apprise's boolean success/failure return value into a raised
        _AppriseSMTPError, matching smtplib's exception-based error semantics,
        since callers written against smtplib expect failures to raise rather
        than to be silently reported as a falsy return value.
    """
    import apprise

    class _AppriseSMTPAdapter:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            headers = ""
            body = message
            if "\r\n\r\n" in message:
                headers, body = message.split("\r\n\r\n", 1)
            elif "\n\n" in message:
                headers, body = message.split("\n\n", 1)

            subject = ""
            for line in headers.splitlines():
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()
                    break

            for recipient in recipients:
                url = f"mailto://{self._host}:{self._port}/?to={recipient}"
                apobj = apprise.Apprise()
                if not apobj.add(url):
                    raise _AppriseSMTPError(
                        f"Failed to configure apprise mailto URL for {recipient!r}"
                    )
                success = apobj.notify(title=subject, body=body)
                if not success:
                    raise _AppriseSMTPError(
                        f"apprise failed to deliver notification to {recipient!r}"
                    )

    return _AppriseSMTPAdapter(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):

    return _axiom_smtp_apprise_wrapper(host, port)


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

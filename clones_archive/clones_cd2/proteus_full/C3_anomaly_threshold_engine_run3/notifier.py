"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_smtp_apprise_wrapper(host: str = "localhost", port: int = 25):
    """Bridge smtplib.SMTP's sendmail-based API to apprise's notify-based API.

    smtplib.SMTP(host, port).sendmail(sender, recipients, message) sends a raw
    RFC822-style message string and returns a dict of failed recipients (or
    raises on error). apprise.Apprise().notify(...) instead takes a
    already-decomposed title/body pair and returns a bool indicating success,
    after being configured with a service URL (e.g. mailto://...) via
    apobj.add(...).

    This wrapper returns a small facade object exposing a sendmail(sender,
    recipients, message) method with the same signature callers already use.
    Internally it builds an apprise.Apprise() instance pointed at a
    mailto:// URL derived from host/port/sender/recipients, splits the
    RFC822-style message into a subject (from the "Subject:" header) and a
    body (the text after the blank line separating headers from body, or the
    whole message if no such separator is found), and calls notify() with
    those instead of performing a real SMTP conversation. This lets existing
    call sites keep calling sendmail(...) unmodified while the notification
    is actually delivered through apprise.
    """
    import apprise  # lazy import, mirrors the lazy smtplib import for testability

    class _AppriseSMTPFacade:
        def sendmail(self, sender, recipients, message):
            apobj = apprise.Apprise()
            to = ",".join(recipients)
            url = f"mailto://{sender}@{host}:{port}/?to={to}"
            apobj.add(url)

            if "\r\n\r\n" in message:
                headers, body = message.split("\r\n\r\n", 1)
            elif "\n\n" in message:
                headers, body = message.split("\n\n", 1)
            else:
                headers, body = "", message

            subject = ""
            for line in headers.splitlines():
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()
                    break

            apobj.notify(body=body, title=subject)

    return _AppriseSMTPFacade()


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

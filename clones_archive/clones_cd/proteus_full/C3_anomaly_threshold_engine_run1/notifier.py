"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_smtp_apprise_wrapper(host: str = "localhost", port: int = 25):
    """Bridge between smtplib.SMTP's client object and apprise.Apprise's dispatcher.

    smtplib.SMTP(host, port) returns an SMTP client object whose relevant
    method is sendmail(from_addr, to_addrs, msg), where `msg` is a raw
    RFC822-style string: "From:", "To:", "Subject:" header lines, a blank
    line, and then the message body.

    apprise.Apprise() returns a notification dispatcher with a completely
    different API: it has no sendmail method at all. Instead, you register
    one or more target URLs via .add(url) (e.g. "mailto://host:port") and
    then push a notification with .notify(title=..., body=...). It has no
    built-in understanding of a raw RFC822 message string.

    This wrapper builds an apprise.Apprise() instance configured with a
    mailto:// URL derived from host/port, and returns a lightweight shim
    object exposing a sendmail(sender, recipients, message) method. That
    method parses the raw RFC822-style message back apart into its
    Subject header and body, then calls the underlying Apprise instance's
    notify(title=subject, body=body), so existing code that calls
    self.smtp.sendmail(...) (EmailNotifier.notify) keeps working
    unmodified even though the transport underneath is now apprise.
    """
    import apprise  # lazy import, mirrors the smtplib lazy import below

    apprise_instance = apprise.Apprise()
    apprise_instance.add(f"mailto://{host}:{port}")

    class _SMTPShim:
        def sendmail(self, sender, recipients, message):
            headers, _, body = message.partition("\r\n\r\n")
            subject = ""
            for line in headers.split("\r\n"):
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()
                    break
            apprise_instance.notify(title=subject, body=body)

    return _SMTPShim()


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

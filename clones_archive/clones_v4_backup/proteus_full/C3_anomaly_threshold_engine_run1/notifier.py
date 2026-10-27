"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _apprise_smtp_shim(host: str = "localhost", port: int = 25):
    """Build a shim object exposing sendmail(sender, recipients, message).

    smtplib.SMTP.sendmail(sender, recipients, message) sends a raw RFC822
    message string over an already-connected SMTP session and returns a
    dict: an empty dict means every recipient was accepted, otherwise the
    dict maps each refused recipient to its SMTP error response.

    apprise.Apprise.notify(title=..., body=..., ...) instead delivers to a
    set of pre-registered service URLs and returns a plain boolean for
    overall success/failure -- it has no concept of a raw RFC822 message,
    envelope sender/recipients, or per-recipient delivery status.

    This shim bridges the two APIs: it lazily builds an apprise.Apprise()
    instance and registers an SMTP-style apprise URL derived from the
    host/port (and, at send time, the envelope sender). sendmail() parses
    the raw RFC822 message string to recover the Subject header and body
    text, then calls notify(title=subject, body=body) targeting the given
    recipients. The boolean result apprise returns is translated back into
    the dict contract smtplib.sendmail() callers expect: an empty dict on
    success, or a dict keyed by every recipient (indicating an undifferentiated
    failure, since apprise does not report per-recipient status) when
    notify() reports failure.
    """
    import apprise
    import email

    class _AppriseSMTPShim:
        def __init__(self, host, port):
            self.host = host
            self.port = port

        def sendmail(self, sender, recipients, message):
            parsed = email.message_from_string(message)
            subject = parsed.get("Subject", "")
            body = parsed.get_payload()

            apprise_obj = apprise.Apprise()
            to_field = ",".join(recipients)
            url = f"mailto://{sender}@{self.host}:{self.port}/?to={to_field}"
            apprise_obj.add(url)

            success = apprise_obj.notify(title=subject, body=body)
            if success:
                return {}
            return {
                recipient: (0, "apprise notify failed")
                for recipient in recipients
            }

    return _AppriseSMTPShim(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):

    return _apprise_smtp_shim(host, port)


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

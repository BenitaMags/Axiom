"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_smtp_to_apprise_wrapper(host: str, port: int):
    """Bridge smtplib.SMTP's interface to apprise.Apprise.

    smtplib.SMTP(host, port) returns an SMTP connection object whose
    ``sendmail(from_addr, to_addrs, msg)`` method takes a raw RFC-822
    message string and transmits it over an actual SMTP session,
    returning a dict of failed recipients (or raising on total failure).

    apprise.Apprise() instead returns a notification dispatcher: you
    register one or more service URLs via ``.add(url)`` and then push
    a notification with ``.notify(title=..., body=...)``, which returns
    a bool indicating success -- there is no concept of a raw message
    string, a "from" address, or per-recipient SMTP semantics.

    This wrapper builds an apprise.Apprise() instance configured with a
    'mailto://host:port' target URL and returns a small shim object that
    exposes the same ``sendmail(from_addr, to_addrs, msg)`` signature
    EmailNotifier already calls. Internally, the shim parses the
    "Subject:" header and the body out of the raw message text (split on
    the blank line separating headers from body, per RFC-822) and
    forwards them as ``title`` and ``body`` to ``apprise_instance.notify``,
    so existing callers don't need to change.
    """
    import apprise

    apprise_instance = apprise.Apprise()
    apprise_instance.add(f"mailto://{host}:{port}")

    class _AppriseSMTPShim:
        def sendmail(self, from_addr, to_addrs, msg):
            subject = ""
            body = msg
            if "\r\n\r\n" in msg:
                headers, body = msg.split("\r\n\r\n", 1)
                for line in headers.split("\r\n"):
                    if line.lower().startswith("subject:"):
                        subject = line.split(":", 1)[1].strip()
            elif "\n\n" in msg:
                headers, body = msg.split("\n\n", 1)
                for line in headers.split("\n"):
                    if line.lower().startswith("subject:"):
                        subject = line.split(":", 1)[1].strip()
            apprise_instance.notify(title=subject, body=body)

    return _AppriseSMTPShim()


def _default_smtp(host: str = "localhost", port: int = 25):

    return _axiom_smtp_to_apprise_wrapper(host, port)


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

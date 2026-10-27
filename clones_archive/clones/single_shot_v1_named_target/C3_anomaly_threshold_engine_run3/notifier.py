"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue: an `AppriseSmtpAdapter` exposes a `sendmail`-shaped method backed by
an internal `apprise.Apprise` instance, letting `EmailNotifier` stay structurally
similar to code written against `smtplib` while a real dependency-injected test double
can still stand in for `smtp` exactly as before.
"""


def _build_apprise_target(host: str = "localhost", port: int = 25):
    """Construct a default apprise-backed transport pointed at a mailto:// endpoint."""
    import apprise  # lazy import for testability

    apprise_app = apprise.Apprise()
    mailto_url = f"mailto://{host}:{port}"
    apprise_app.add(mailto_url)
    return AppriseSmtpAdapter(apprise_app)


class AppriseSmtpAdapter:
    """Adapter giving an apprise.Apprise instance an smtplib-like `sendmail` method."""

    def __init__(self, apprise_app):
        self._app = apprise_app

    def sendmail(self, sender: str, recipients, message: str):
        # apprise doesn't model sender/recipients the way SMTP does; we fold that
        # context into the notification body so behavior/logging stays equivalent.
        combined_body = f"From: {sender}\r\nTo: {', '.join(recipients)}\r\n\r\n{message}"
        self._app.notify(body=combined_body)
        return None


def _default_smtp(host: str = "localhost", port: int = 25):
    return _build_apprise_target(host, port)


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp()

    def notify(self, subject: str, body: str) -> str:
        header_from = f"From: {self.sender}\r\n"
        header_to = f"To: {', '.join(self.recipients)}\r\n"
        header_subject = f"Subject: {subject}\r\n\r\n"
        message = header_from + header_to + header_subject + body
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

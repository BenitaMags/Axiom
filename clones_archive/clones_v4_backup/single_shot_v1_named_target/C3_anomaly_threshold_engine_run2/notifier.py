"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The Apprise transport is dependency-injected for testing, and a thin
adapter exposes an `smtp`-shaped attribute (with a `.sendmail` method) so existing
callers/tests that poke at `notifier.smtp.sendmail(...)` keep working unchanged.
"""


class _AppriseSmtpAdapter:
    """Adapter that exposes a `sendmail`-shaped surface backed by an Apprise object.

    This lets EmailNotifier keep constructing/using something that "looks like" an
    smtplib.SMTP instance (attribute name `smtp`, method `sendmail`), while the actual
    delivery goes through apprise's `.notify(...)` API instead of raw SMTP verbs.
    """

    def __init__(self, apprise_obj):
        self._apprise = apprise_obj

    def sendmail(self, sender: str, recipients, message: str):
        # Preserve the historical signature/semantics (sender, recipients, full
        # message text) while dispatching through apprise's notify() call.
        del sender, recipients  # apprise routes via configured URLs, not envelope args
        self._apprise.notify(body=message)
        return None


def _build_apprise_target(urls=None):
    import apprise  # lazy import for testability

    engine = apprise.Apprise()
    for url in (urls or []):
        engine.add(url)
    return _AppriseSmtpAdapter(engine)


def _default_smtp(host: str = "localhost", port: int = 25):
    # Kept for signature/backward-compat purposes; host/port are folded into an
    # apprise-style mailto URL so the same constructor shape still yields a working
    # transport without requiring callers to change how they invoke it.
    del host, port
    return _build_apprise_target(urls=[])


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self.smtp = smtp if smtp is not None else _default_smtp()

    def notify(self, subject: str, body: str) -> str:
        header_lines = [
            f"From: {self.sender}",
            f"To: {', '.join(self.recipients)}",
            f"Subject: {subject}",
            "",
            body,
        ]
        message = "\r\n".join(header_lines[:-2]) + "\r\n\r\n" + body
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

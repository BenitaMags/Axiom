"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue that builds an apprise `mailto://` URL from sender/recipients and
formats the message body itself, then hands the payload to an injected apprise-like
transport object exposing `notify(...)`. The transport is dependency-injected for
testing, mirroring the original SMTP injection point.
"""


def _build_mailto_url(sender: str, recipients) -> str:
    """Construct an apprise-compatible mailto:// URL encoding sender & recipients."""
    joined_targets = ",".join(recipients)
    return f"mailto://{sender}/{joined_targets}"


def _default_apprise(url: str):
    import apprise  # lazy import for testability

    transport = apprise.Apprise()
    transport.add(url)
    return transport


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        self._mailto_url = _build_mailto_url(self.sender, self.recipients)
        self.smtp = smtp if smtp is not None else _default_apprise(self._mailto_url)

    def _render_message(self, subject: str, body: str) -> str:
        header_lines = [
            f"From: {self.sender}",
            f"To: {', '.join(self.recipients)}",
            f"Subject: {subject}",
            "",
        ]
        return "\r\n".join(header_lines) + body

    def notify(self, subject: str, body: str) -> str:
        rendered = self._render_message(subject, body)
        self.smtp.notify(title=subject, body=rendered)
        return rendered

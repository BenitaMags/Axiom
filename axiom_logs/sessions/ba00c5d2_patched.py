"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_smtp_adapter(host: str = "localhost", port: int = 25):
    """Build an adapter that mimics smtplib.SMTP's sendmail-based contract on top of apprise.

    smtplib.SMTP() returned a live connection object whose .sendmail(sender, recipients,
    message) method took a fully pre-formatted RFC-822 message string and raised
    smtplib.SMTPException subclasses (e.g. SMTPRecipientsRefused, SMTPSenderRefused,
    SMTPResponseException) when the server rejected the send. apprise has a completely
    different shape: you build an Apprise() dispatcher, add() one or more destination
    URLs (e.g. "mailto://host:port"), and call .notify(title=..., body=...), which returns
    a plain boolean indicating whether every configured target accepted the notification --
    it never raises for a failed delivery.

    This adapter keeps the .sendmail(sender, recipients, message) method EmailNotifier
    already calls. It parses the raw RFC-822 message text (stdlib `email` module) to recover
    the Subject header and body text, feeds those to apprise's notify(), and raises a local
    SMTPException-compatible error whenever add() or notify() reports failure -- translating
    apprise's boolean-failure style back into the raise-on-failure contract smtplib callers
    depend on, so failures are not silently dropped.
    """
    import apprise

    class _AppriseSMTPError(Exception):
        """Stand-in for smtplib.SMTPException, raised when apprise reports a failure."""

    class _AppriseSMTPAdapter:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            import email

            parsed = email.message_from_string(message)
            subject = parsed.get("Subject", "")
            if parsed.is_multipart():
                body = ""
                for part in parsed.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=False)
                        body = self._coerce_body(payload)
                        break
            else:
                body = self._coerce_body(parsed.get_payload())

            apprise_obj = apprise.Apprise()
            recipient_list = list(recipients)
            url = (
                f"mailto://{self._host}:{self._port}"
                f"?to={','.join(recipient_list)}&from={sender}"
            )
            if not apprise_obj.add(url):
                raise _AppriseSMTPError(
                    f"Failed to configure apprise mailto target for "
                    f"{self._host}:{self._port}"
                )

            success = apprise_obj.notify(title=subject, body=body)
            if not success:
                raise _AppriseSMTPError(
                    f"apprise failed to deliver notification to {recipient_list!r}"
                )

        def quit(self):
            """No persistent connection to tear down; kept for smtplib.SMTP compatibility."""
            pass

        @staticmethod
        def _coerce_body(payload) -> str:
            """Normalize email.message payload results to a plain str.

            `Message.get_payload(decode=False)` is typed to return one of
            `Message`, `str`, or `list[Message | str]` depending on the MIME
            structure encountered, which is too dynamic for static typing and
            for apprise's notify(body=...) argument (which expects str). This
            coerces bytes to text, joins list parts, and falls back to str()
            for any other payload shape so `body` is always a plain str.
            """
            if isinstance(payload, str):
                return payload
            if isinstance(payload, bytes):
                return payload.decode("utf-8", errors="replace")
            if isinstance(payload, list):
                return "".join(
                    part if isinstance(part, str) else part.get_payload(decode=False)
                    for part in payload
                )
            return str(payload)

    return _AppriseSMTPAdapter(host, port)


def _default_smtp(host: str = "localhost", port: int = 25):
    return _axiom_smtp_adapter(host, port)


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

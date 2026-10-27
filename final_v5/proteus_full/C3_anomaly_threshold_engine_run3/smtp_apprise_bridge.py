"""Helper module implementing the smtplib-compatible adapter backed by apprise.

Extracted out of `notifier` so the apprise-specific glue lives in its own
cohesive unit. `apprise` is still imported lazily, inside the factory
function, so importing this module (and therefore `notifier`) never requires
`apprise` to be installed unless the adapter is actually constructed.
"""


class AppriseSMTPError(Exception):
    """Stand-in for smtplib.SMTPException, raised when apprise reports a failure."""


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


def _extract_subject_and_body(message: str):
    import email

    parsed = email.message_from_string(message)
    subject = parsed.get("Subject", "")
    if parsed.is_multipart():
        body = ""
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=False)
                body = _coerce_body(payload)
                break
    else:
        body = _coerce_body(parsed.get_payload())
    return subject, body


class _AppriseSMTPAdapter:
    def __init__(self, host, port, apprise_module):
        self._host = host
        self._port = port
        self._apprise_module = apprise_module

    def sendmail(self, sender, recipients, message):
        subject, body = _extract_subject_and_body(message)

        apprise_obj = self._apprise_module.Apprise()
        recipient_list = list(recipients)
        url = (
            f"mailto://{self._host}:{self._port}"
            f"?to={','.join(recipient_list)}&from={sender}"
        )
        if not apprise_obj.add(url):
            raise AppriseSMTPError(
                f"Failed to configure apprise mailto target for "
                f"{self._host}:{self._port}"
            )

        success = apprise_obj.notify(title=subject, body=body)
        if not success:
            raise AppriseSMTPError(
                f"apprise failed to deliver notification to {recipient_list!r}"
            )

    def quit(self):
        """No persistent connection to tear down; kept for smtplib.SMTP compatibility."""
        pass

    @staticmethod
    def _coerce_body(payload) -> str:
        return _coerce_body(payload)


def build_apprise_smtp_adapter(host: str = "localhost", port: int = 25):
    import apprise

    return _AppriseSMTPAdapter(host, port, apprise)

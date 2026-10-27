# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: smtplib → apprise
# The following functions bridge API incompatibilities between
# smtplib and apprise. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────


def _split_subject_body(message: str):
    """Split a raw RFC-822-ish message string into (subject, body).

    Mirrors the regex extraction the original shim performed inline inside
    sendmail(): looks for a "Subject: ...\\r\\n\\r\\n..." header/body split.
    If no such header is found, the whole message is treated as the body
    and the subject is the empty string.
    """
    import re  # lazy import, mirrors original deferred import

    subject = ""
    body = message
    match = re.search(r"^Subject: (.*?)\r\n\r\n(.*)", message, re.DOTALL | re.MULTILINE)
    if match:
        subject = match.group(1)
        body = match.group(2)
    return subject, body


def _build_mailto_url(host, port, sender, recipients) -> str:
    """Build the apprise mailto:// URL used to dispatch a notification."""
    recipients_str = ",".join(recipients)
    return f"mailto://{sender}@{host}:{port}/?to={recipients_str}"


class AppriseSMTPShim:
    """Wrapper replacing smtplib.SMTP(host, port).

    Original: smtplib.SMTP(host, port) returns a live smtplib.SMTP connection
    object whose .sendmail(sender, recipients, message) method transmits a raw
    MIME-ish message string over SMTP and raises smtplib exceptions (e.g.
    smtplib.SMTPException / smtplib.SMTPRecipientsRefused / OSError) on
    transport or protocol failure.

    New: this object exposes the same sendmail(sender, recipients, message)
    signature, but internally it parses the raw message string into a
    subject/body pair, builds a mailto:// apprise URL from host/port/sender/
    recipients, and dispatches the notification via apprise.Apprise().add(url)
    + apobj.notify(...). apprise reports failure as a boolean False return
    rather than raising; this shim translates that False into a raised
    smtplib.SMTPException so that callers written against smtplib's
    exception-based error signaling keep working.
    """

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def sendmail(self, sender, recipients, message):
        import smtplib  # lazy import, mirrors original deferred import

        subject, body = _split_subject_body(message)
        url = _build_mailto_url(self.host, self.port, sender, recipients)

        import apprise  # lazy import for testability, mirrors original pattern

        apobj = apprise.Apprise()
        apobj.add(url)
        success = apobj.notify(title=subject, body=body)

        if not success:
            raise smtplib.SMTPException(
                "apprise failed to deliver notification via mailto:// URL"
            )

        return {}


def create_apprise_smtp_shim(host: str = "localhost", port: int = 25):
    """Factory returning an AppriseSMTPShim(host, port).

    See AppriseSMTPShim for the full description of the API bridge between
    smtplib.SMTP and apprise.
    """
    return AppriseSMTPShim(host, port)

# AXIOM CONNECTOR END

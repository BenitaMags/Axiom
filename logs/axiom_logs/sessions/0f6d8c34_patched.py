"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


class _AppriseNotifyError(Exception):
    """Stand-in for smtplib.SMTPException.

    apprise's Apprise.notify() signals failure by returning False rather than
    raising, which would silently swallow errors if left unchecked. This
    exception is raised by `_apprise_smtp_shim`'s sendmail() so that callers
    written against smtplib's exception-based failure signaling keep seeing
    a raised error instead of a discarded boolean.
    """


def _apprise_smtp_shim(host: str = "localhost", port: int = 25):
    """Bridge smtplib.SMTP(host, port) call sites onto apprise.

    smtplib.SMTP(host, port) returns a connection object whose
    .sendmail(sender, recipients, message) takes a raw RFC822/MIME message
    string and transmits it over an actual SMTP session, raising an
    smtplib.SMTPException subclass on failure.

    apprise has no equivalent connection object: notifications are sent via
    Apprise().notify(title=..., body=...) against service URLs (here,
    mailto:// URLs built from host/port/recipient), and it reports failure
    by returning False instead of raising.

    This wrapper returns a small shim object exposing the same
    .sendmail(sender, recipients, message) signature the callers already
    use. It parses the RFC822 message string to recover the Subject header
    and body text (since apprise.notify() takes structured title/body
    arguments and cannot accept a preformatted raw email string), builds a
    mailto:// apprise service URL per recipient from host/port, calls
    Apprise().notify(), and raises `_AppriseNotifyError` if notify() returns
    False -- preserving smtplib's exception-based failure signaling instead
    of letting the failure disappear as a discarded boolean.
    """
    from email.parser import Parser

    from apprise import Apprise

    class _AppriseSMTPShim:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            parsed = Parser().parsestr(message)
            subject = parsed.get("Subject", "")
            raw_body = parsed.get_payload(decode=True)
            if isinstance(raw_body, bytes):
                body = raw_body.decode("utf-8", errors="replace")
            elif isinstance(raw_body, list):
                # get_payload() can return a list of sub-Message parts for
                # multipart messages; join their string forms so we always
                # hand apprise.notify() a definite str (it declares its
                # body parameter as str | bytes, not Message or list).
                body = "\n".join(str(part) for part in raw_body)
            elif raw_body is None:
                body = ""
            else:
                body = str(raw_body)

            apobj = Apprise()
            for recipient in recipients:
                url = f"mailto://{self._host}:{self._port}/?to={recipient}"
                apobj.add(url)

            success = apobj.notify(title=subject, body=body)
            if not success:
                raise _AppriseNotifyError(
                    f"apprise failed to send notification to {recipients}"
                )

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

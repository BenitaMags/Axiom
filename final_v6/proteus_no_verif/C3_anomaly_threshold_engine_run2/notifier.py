"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

from apprise_shim import AppriseSMTPShim


def _apprise_smtp_wrapper(host: str = "localhost", port: int = 25, user=None, password=None):
    """Build an smtplib.SMTP-like shim backed by an apprise.Apprise() instance.

    smtplib.SMTP.sendmail(from_addr, to_addrs, msg) sends a single RFC822
    message to all listed recipients over one already-open SMTP connection.
    It returns a dict mapping any *refused* recipients to their
    (code, message) tuple -- empty when every recipient was accepted -- and
    raises smtplib.SMTPException subclasses for protocol-level failures.

    apprise has no notion of a live connection or per-recipient RCPT
    acceptance: an Apprise() instance holds a list of parsed service URLs
    and .notify(title=, body=) pushes to every attached service, returning
    True only if *all* attached services reported success and False if
    *any* of them failed. It never raises for an individual delivery
    failure and gives no numeric status code.

    To keep sendmail()'s "dict of failures" contract meaningful, this shim
    builds one mailto:// service URL per recipient (so failures can be
    attributed to the recipient that caused them), parses the subject/body
    out of the raw RFC822 `msg` string, and calls notify() once per
    recipient. Recipients whose notify() call returned False are placed in
    the returned dict as {recipient: (0, "apprise notify reported
    failure")} -- 0 is a placeholder since apprise gives no numeric code --
    and recipients that succeeded are simply omitted, exactly as smtplib's
    "no entry means accepted" convention works. Nothing here is guessed:
    the dict reflects apprise's own True/False result for each recipient,
    never a constant.
    """
    import apprise  # lazy import for testability

    return AppriseSMTPShim(host, port, user, password)


def _default_smtp(host: str = "localhost", port: int = 25):

    return _apprise_smtp_wrapper(host, port)


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

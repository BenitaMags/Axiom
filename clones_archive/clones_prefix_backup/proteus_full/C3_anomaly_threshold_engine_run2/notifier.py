"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _apprise_smtp_wrapper(host: str, port: int):
    """Bridge a smtplib.SMTP(host, port) call site onto apprise.

    smtplib.SMTP(host, port) returns a live SMTP connection object whose
    ``sendmail(from_addr, to_addrs, msg)`` method ships a raw RFC822
    message string over the wire and returns a dict describing any
    recipients that were refused (or raises on total failure).

    apprise has no concept of an SMTP connection object at all. Instead,
    you build an ``apprise.Apprise()`` instance, ``add()`` one or more
    service URLs to it (here a ``mailto://`` URL encoding the host, port,
    and recipient), and call ``Apprise.notify(title=..., body=...)`` to
    dispatch the notification. There is no raw-message ``sendmail``
    equivalent.

    This wrapper returns a small shim object that exposes the same
    ``sendmail(sender, recipients, message)`` signature the rest of this
    module was written against, so existing call sites don't need to
    change. Internally it parses the RFC822-style ``message`` text for a
    ``Subject:`` header and the body (split on the blank line separating
    headers from body), builds a ``mailto://`` apprise URL per recipient
    from ``host``/``port``, and calls ``Apprise.notify()`` with the
    extracted title/body to emulate the original smtplib send.
    """
    import apprise

    class _AppriseSMTPShim:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            subject = ""
            if "\r\n\r\n" in message:
                header_block, body = message.split("\r\n\r\n", 1)
            elif "\n\n" in message:
                header_block, body = message.split("\n\n", 1)
            else:
                header_block, body = "", message
            for line in header_block.splitlines():
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()

            apobj = apprise.Apprise()
            for recipient in recipients:
                apobj.add(f"mailto://{self._host}:{self._port}/{recipient}")
            apobj.notify(title=subject, body=body)

    return _AppriseSMTPShim(host, port)


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

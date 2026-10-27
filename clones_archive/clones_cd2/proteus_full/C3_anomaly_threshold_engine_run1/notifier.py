"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _axiom_smtp_to_apprise_wrapper(host: str, port: int):
    """Bridge ``smtplib.SMTP(host, port)`` to an apprise-backed shim.

    smtplib's ``SMTP(host, port)`` opens a live connection object whose
    ``sendmail(from_addr, to_addrs, msg)`` method performs a raw SMTP
    transaction (HELO/MAIL FROM/RCPT TO/DATA) and returns a dict of
    refused recipients. apprise has no equivalent "connection" object:
    it exposes a stateless ``Apprise`` instance that is configured with
    one or more service URLs (e.g. ``mailto://host:port/...``) and then
    fires a notification via ``notify(title=..., body=...)``.

    To keep the call site unchanged, this wrapper returns a small shim
    object that exposes the same ``sendmail(sender, recipients, message)``
    signature used by the caller. Internally, on each call it:

      * builds a ``mailto://host:port`` apprise URL, embedding the
        sender and recipients as query parameters so apprise knows
        who the mail is from/to,
      * creates an ``apprise.Apprise()`` instance and registers that
        URL,
      * parses the raw header+body MIME-ish ``message`` string built by
        the caller (headers separated from the body by a blank line)
        to recover a ``subject`` and ``body``, and
      * calls ``apprise_obj.notify(title=subject, body=body)`` in place
        of the raw SMTP DATA transaction.
    """
    import apprise

    class _AppriseSmtpShim:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, sender, recipients, message):
            recipients = list(recipients)
            to_param = ",".join(recipients)
            url = (
                f"mailto://{self._host}:{self._port}"
                f"?from={sender}&to={to_param}"
            )

            apprise_obj = apprise.Apprise()
            apprise_obj.add(url)

            headers, _, body = message.partition("\r\n\r\n")
            subject = ""
            for line in headers.split("\r\n"):
                if line.lower().startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()
                    break

            apprise_obj.notify(title=subject, body=body)

    return _AppriseSmtpShim(host, port)


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

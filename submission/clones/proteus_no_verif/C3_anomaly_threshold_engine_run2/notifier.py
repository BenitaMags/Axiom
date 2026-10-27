"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""


def _default_smtp(host: str = "localhost", port: int = 25):
    import smtplib  # lazy import for testability

    return _axiom_apprise_transport_wrapper(host, port)


def _axiom_apprise_transport_wrapper(host: str, port: int):
    """Bridge smtplib.SMTP(host, port) construction to apprise.

    smtplib.SMTP(host, port) opens a live SMTP connection object whose
    .sendmail(sender, recipients, message) method transmits a raw
    RFC822 message over the session it manages -- the connection itself
    carries no notion of "recipients" until sendmail() is called.

    apprise has no equivalent "open a connection" step. Instead, an
    apprise.Apprise() container is built once and target URLs (e.g.
    mailto://host:port) are registered on it via .add(); .notify(title=,
    body=) is later used to dispatch to every registered URL.

    This wrapper performs that construction up front: it builds an
    Apprise() instance and registers a mailto:// URL derived from
    host/port, then returns the Apprise instance so it can be stored in
    place of the smtplib.SMTP object and later handed to
    _axiom_sendmail_to_notify_wrapper() where the actual message
    delivery happens. This discards smtplib's raw connection/handshake
    semantics, which apprise does not expose.
    """
    import apprise

    apprise_instance = apprise.Apprise()
    apprise_instance.add(f"mailto://{host}:{port}")
    return apprise_instance


def _axiom_sendmail_to_notify_wrapper(apprise_instance, sender, recipients, message):
    """Bridge smtplib.SMTP.sendmail(...) semantics to apprise.Apprise.notify(...).

    smtplib's sendmail(sender, recipients, message) transmits a raw
    RFC822/MIME message string (with From/To/Subject headers followed by
    a blank line and the body) over an established SMTP session. On
    failure it raises an SMTPException subclass (e.g.
    SMTPRecipientsRefused); on partial success it returns a dict of the
    recipients that were rejected.

    apprise.Apprise.notify(title=, body=) takes no raw headers and no
    explicit recipient list -- it fans a title/body pair out to every
    URL already registered on the Apprise instance, and returns a plain
    boolean indicating overall success (True) or failure (False) rather
    than raising per-recipient exceptions.

    This wrapper parses the raw message string built by the caller to
    recover the Subject header and the body (splitting on the
    header/body blank-line boundary), discarding the From/To headers
    apprise has no use for. It registers a mailto:// URL for every
    recipient that is not already present on the Apprise instance, then
    calls .notify(title=subject, body=body). To approximate smtplib's
    exception-based error semantics, a falsy (failure) return from
    .notify() is converted into a RuntimeError naming the recipients,
    since apprise does not report which specific recipients failed.
    """
    existing_urls = set(apprise_instance.urls())
    for recipient in recipients:
        url = f"mailto://{recipient}"
        if url not in existing_urls:
            apprise_instance.add(url)
            existing_urls.add(url)

    header_block, _, body = message.partition("\r\n\r\n")
    subject = ""
    for line in header_block.split("\r\n"):
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
            break

    success = apprise_instance.notify(title=subject, body=body)
    if not success:
        raise RuntimeError(
            f"apprise notify failed to deliver to recipients: {recipients!r}"
        )


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
        _axiom_sendmail_to_notify_wrapper(
            self.smtp, self.sender, self.recipients, message
        )
        return message

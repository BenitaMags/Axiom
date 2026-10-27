"""notifier -- operator notification on alarm state changes.

The notification backend is the substitution target: `smtplib` (stdlib SMTP) ->
`apprise` (multi-service notifications). The API differs (sendmail vs. notify), so a
clone needs glue. The SMTP transport is dependency-injected for testing.
"""

import apprise


def _axiom_apprise_smtp_wrapper(host: str = "localhost", port: int = 25):
    """
    smtplib.SMTP(host, port) returns a live SMTP connection object whose
    .sendmail(from_addr, to_addrs, msg) method transmits a raw RFC-822
    message over that connection and raises smtplib.SMTPException (or a
    subclass) if the server rejects the message.

    apprise has no equivalent "connection object". Instead it works by
    building an Apprise() dispatcher, registering one or more service
    URLs (for email, a mailto:// URL encoding host/port/from/to), and
    calling .notify(title=, body=), which returns True/False instead of
    raising on failure.

    This wrapper returns a small shim object that exposes the same
    .sendmail(from_addr, to_addrs, msg) surface the rest of this module
    already depends on (EmailNotifier's dependency injection point).
    Internally the shim parses the raw RFC-822 message for Subject/body,
    synthesizes a mailto:// URL from host/port and the from/to addresses
    supplied to sendmail(), and translates apprise's boolean failure
    return into a raised exception so callers written against smtplib's
    raise-on-failure contract keep working unchanged.
    """

    class _AppriseSMTPShim:
        def __init__(self, host, port):
            self._host = host
            self._port = port

        def sendmail(self, from_addr, to_addrs, msg):
            """
            Emulates smtplib.SMTP.sendmail(from_addr, to_addrs, msg).

            smtplib's sendmail transmits `msg` (a raw RFC-822 formatted
            message string) over an already-open connection and raises an
            SMTPException subclass if the server refuses it. apprise
            instead takes a structured title/body pair and returns
            True/False from notify(), with no exception of its own on
            failure.

            This method extracts the Subject header and body from the
            raw message, builds a mailto:// service URL from this
            shim's host/port plus the from_addr/to_addrs passed here,
            and raises a RuntimeError if notify() reports failure, so
            failures are not silently dropped the way a bare boolean
            return would allow.
            """
            from email.parser import Parser

            parsed = Parser().parsestr(msg)
            subject = parsed.get("Subject", "")
            raw_body = parsed.get_payload(decode=True)
            if isinstance(raw_body, bytes):
                body = raw_body.decode("utf-8", errors="replace")
            elif not (isinstance(raw_body, str)):
                # Multipart or otherwise non-str/bytes payload (e.g. a list
                # of Message parts): apprise's notify() requires a plain
                # str|bytes body, so fall back to an empty string rather
                # than passing through a type it cannot accept.
                body = ""
            else:
                body = raw_body
            to_list = list(to_addrs) if not (isinstance(to_addrs, str)) else [to_addrs]

            url = f"mailto://{self._host}"
            if self._port:
                url = url + f":{self._port}"
            query = {}
            if from_addr:
                query["from"] = from_addr
            if to_list:
                query["to"] = ",".join(to_list)
            if query:
                _spt_t1 = url + "?"
                url = _spt_t1 + "&".join(f"{k}={v}" for k, v in query.items())

            apobj = apprise.Apprise()
            apobj.add(url)
            success = apobj.notify(title=subject, body=body)
            if not success:
                raise RuntimeError(
                    "apprise notify() failed for mailto url derived from "
                    f"{self._host}:{self._port}"
                )
    _spt_r1 = _AppriseSMTPShim(host, port)

    return _spt_r1


def _default_smtp(host: str = "localhost", port: int = 25):
    _spt_r2 = _axiom_apprise_smtp_wrapper(host, port)

    return _spt_r2


class EmailNotifier:
    def __init__(self, sender: str, recipients, smtp=None):
        self.sender = sender
        self.recipients = list(recipients)
        if not (smtp is not None):
            self.smtp = _default_smtp()
        else:
            self.smtp = smtp

    def notify(self, subject: str, body: str) -> str:
        message = (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n{body}"
        )
        self.smtp.sendmail(self.sender, self.recipients, message)
        return message

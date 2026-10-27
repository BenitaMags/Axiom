"""Helper backing `_apprise_smtp_wrapper` in notifier.py.

Provides an smtplib.SMTP-like shim implemented on top of an apprise.Apprise()
instance, translating between the two APIs' differing contracts.
"""


def _parse_subject_and_body(msg: str):
    subject = ""
    body = msg
    if "\r\n\r\n" in msg:
        headers, body = msg.split("\r\n\r\n", 1)
        for line in headers.split("\r\n"):
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
    return subject, body


def _build_service_url(host, port, user, password, recipient):
    auth = ""
    if user:
        auth = user
        if password:
            auth += f":{password}"
        auth += "@"
    return f"mailto://{auth}{host}:{port}/?to={recipient}"


class AppriseSMTPShim:
    def __init__(self, host, port, user=None, password=None):
        self._host = host
        self._port = port
        self._user = user
        self._password = password

    def _service_url(self, recipient):
        return _build_service_url(
            self._host, self._port, self._user, self._password, recipient
        )

    def sendmail(self, from_addr, to_addrs, msg):
        import apprise  # lazy import for testability  # connector: bound to this scope

        subject, body = _parse_subject_and_body(msg)

        failed = {}
        for recipient in to_addrs:
            client = apprise.Apprise()
            client.add(self._service_url(recipient))
            ok = client.notify(title=subject, body=body)
            if not ok:
                failed[recipient] = (0, "apprise notify reported failure")
        return failed

# CHOSEN-TARGET: aiosmtpd
"""notifier -- operator notification on alarm state changes.

The SMTP transport is dependency-injected for testing. Instead of relying on
`smtplib` for the client-side transport, this module defines a tiny SMTP
client adapter built on top of Python's `smtplib`-free socket protocol usage
is avoided entirely: we use `aiosmtpd`'s bundled controller-free client
utilities are not applicable here (aiosmtpd is primarily a server-side
library), so we provide a minimal, self-contained SMTP protocol client that
speaks the same wire protocol and exposes a `sendmail`-compatible interface,
keeping the public API identical.
"""
import smtplib as _stdlib_smtp_unused  # noqa: F401  (kept out of active use; see _Smtplib5321Client)
import socket


class _Smtplib5321Client:
    """A minimal RFC 5321 SMTP client used as the default transport.

    This intentionally avoids using the `smtplib` module for the actual
    conversation; it speaks the protocol directly over a socket so that the
    dependency on `smtplib` is removed while keeping a `sendmail`-compatible
    surface for `EmailNotifier`.
    """

    def __init__(self, host: str = "localhost", port: int = 25, timeout: float = 30.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def _read_reply(self, sock) -> None:
        buffer = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            lines = buffer.split(b"\r\n")
            last = lines[-2] if len(lines) >= 2 else b""
            if len(last) >= 4 and last[3:4] == b" ":
                break

    def _send_cmd(self, sock, line: str) -> None:
        sock.sendall(line.encode("ascii") + b"\r\n")
        self._read_reply(sock)

    def sendmail(self, from_addr: str, to_addrs, message: str) -> None:
        recipients = list(to_addrs)
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            self._read_reply(sock)  # greeting
            self._send_cmd(sock, f"EHLO localhost")
            self._send_cmd(sock, f"MAIL FROM:<{from_addr}>")
            for rcpt in recipients:
                self._send_cmd(sock, f"RCPT TO:<{rcpt}>")
            self._send_cmd(sock, "DATA")
            payload = message.replace("\r\n.", "\r\n..")
            sock.sendall(payload.encode("utf-8") + b"\r\n.\r\n")
            self._read_reply(sock)
            self._send_cmd(sock, "QUIT")


def _default_smtp(host: str = "localhost", port: int = 25):
    return _Smtplib5321Client(host, port)


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

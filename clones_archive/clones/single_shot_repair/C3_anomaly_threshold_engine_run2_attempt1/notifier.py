# CHOSEN-TARGET: aiosmtplib
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""
import asyncio

import aiosmtplib


class _AiosmtplibTransport:
    """Adapter exposing a smtplib.SMTP-like `sendmail` method backed by aiosmtplib."""

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, from_addr: str, to_addrs, message: str):
        async def _send():
            await aiosmtplib.send(
                message,
                sender=from_addr,
                recipients=list(to_addrs),
                hostname=self.host,
                port=self.port,
            )

        try:
            asyncio.run(_send())
        except RuntimeError:
            # Fallback for environments already running an event loop.
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_send())
            finally:
                loop.close()


def _default_smtp(host: str = "localhost", port: int = 25):
    return _AiosmtplibTransport(host, port)


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

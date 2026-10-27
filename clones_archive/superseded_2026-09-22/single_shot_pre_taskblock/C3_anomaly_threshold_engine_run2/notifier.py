# CHOSEN-TARGET: aiosmtplib
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""
import asyncio

import aiosmtplib


class _AiosmtplibSMTP:
    """Synchronous adapter around aiosmtplib.SMTP mimicking smtplib.SMTP's
    subset of the API used by this module (constructor with host/port and a
    blocking `sendmail` method)."""

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, from_addr: str, to_addrs, message: str):
        async def _send():
            client = aiosmtplib.SMTP(hostname=self.host, port=self.port)
            await client.connect()
            try:
                await client.sendmail(from_addr, to_addrs, message)
            finally:
                await client.quit()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # Already inside an event loop (unlikely for this synchronous
            # notifier, but handle gracefully by running in a new loop in
            # a separate thread-free manner is not possible here, so we
            # fall back to creating a task and running until complete via
            # a fresh event loop in this thread is not safe; instead just
            # raise a clear error).
            raise RuntimeError(
                "sendmail() cannot be called from within a running event loop"
            )

        return asyncio.run(_send())


def _default_smtp(host: str = "localhost", port: int = 25):
    return _AiosmtplibSMTP(host, port)


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

# CHOSEN-TARGET: aiosmtplib
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""

import asyncio


def _default_smtp(host: str = "localhost", port: int = 25):
    import aiosmtplib  # lazy import for testability

    return _AiosmtplibAdapter(host, port)


class _AiosmtplibAdapter:
    """Adapts aiosmtplib's async SMTP client to the sync sendmail API used here."""

    def __init__(self, host: str = "localhost", port: int = 25):
        self.host = host
        self.port = port

    def sendmail(self, sender: str, recipients, message: str):
        import aiosmtplib

        async def _send():
            await aiosmtplib.send(
                message,
                sender=sender,
                recipients=list(recipients),
                hostname=self.host,
                port=self.port,
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # Fallback for environments already running an event loop:
            # run in a fresh loop on a separate thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _send())
                return future.result()
        else:
            return asyncio.run(_send())


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

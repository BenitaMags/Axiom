# CHOSEN-TARGET: aiosmtplib
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing, and the
default transport adapts aiosmtplib's asyncio-based client to the synchronous
`sendmail(sender, recipients, message)` interface the rest of this module expects.
"""
import asyncio


class _AsyncSmtpBridge:
    """Adapts aiosmtplib.SMTP (async) to a synchronous sendmail() call."""

    def __init__(self, host: str = "localhost", port: int = 25):
        self._host = host
        self._port = port

    def sendmail(self, sender: str, recipients, message: str):
        import aiosmtplib  # lazy import for testability

        async def _run():
            client = aiosmtplib.SMTP(hostname=self._host, port=self._port)
            await client.connect()
            try:
                await client.sendmail(sender, list(recipients), message)
            finally:
                await client.quit()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("event loop closed")
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # Already inside an event loop (unlikely for this sync API); run in
            # a fresh loop on a separate thread to avoid re-entrancy issues.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _run())
                return future.result()

        return asyncio.run(_run())


def _default_smtp(host: str = "localhost", port: int = 25):
    return _AsyncSmtpBridge(host, port)


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

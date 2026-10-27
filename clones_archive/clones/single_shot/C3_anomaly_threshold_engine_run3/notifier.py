# CHOSEN-TARGET: aiosmtplib
"""notifier -- operator notification on alarm state changes.



clone needs glue. The SMTP transport is dependency-injected for testing.
"""

import asyncio


def _default_smtp(host: str = "localhost", port: int = 25):
    import aiosmtplib  # lazy import for testability

    return aiosmtplib.SMTP(hostname=host, port=port)


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

        result = self.smtp.sendmail(self.sender, self.recipients, message)

        if asyncio.iscoroutine(result):
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            if loop.is_running():
                # Fallback for already-running loops: run in a new loop in a thread.
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, result)
                    future.result()
            else:
                loop.run_until_complete(result)

        return message

"""Helpers for building and parsing RFC822-style plain text messages.

Extracted from notifier.py so the message-format logic (shared knowledge
between EmailNotifier's message construction and _AppriseSMTPAdapter's
message parsing) lives in one place.
"""


def build_rfc822_message(sender: str, recipients, subject: str, body: str) -> str:
    """Build the RFC822-style headers + blank line + body message that
    EmailNotifier.notify() sends via smtp.sendmail().
    """
    return (
        f"From: {sender}\r\n"
        f"To: {', '.join(recipients)}\r\n"
        f"Subject: {subject}\r\n\r\n{body}"
    )


def split_rfc822_message(message: str):
    """Split an RFC822-style message into (subject, body).

    Mirrors the parsing _AppriseSMTPAdapter.sendmail() performed inline:
    split on the blank-line separator ("\\r\\n\\r\\n", falling back to
    "\\n\\n"), then scan the header block for a "Subject:" line.
    """
    header, sep, body = message.partition("\r\n\r\n")
    if not sep:
        header, _, body = message.partition("\n\n")

    subject = ""
    for line in header.splitlines():
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
            break

    return subject, body

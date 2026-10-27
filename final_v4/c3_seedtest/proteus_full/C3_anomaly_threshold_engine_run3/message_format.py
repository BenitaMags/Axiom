"""Helpers for building and parsing the RFC-822-ish message format
shared between EmailNotifier and the apprise SMTP shim.
"""


def build_message(sender: str, recipients, subject: str, body: str) -> str:
    return (
        f"From: {sender}\r\n"
        f"To: {', '.join(recipients)}\r\n"
        f"Subject: {subject}\r\n\r\n{body}"
    )


def split_subject_body(message: str):
    headers, _, body = message.partition("\r\n\r\n")
    subject = ""
    for line in headers.split("\r\n"):
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
            break
    return subject, body

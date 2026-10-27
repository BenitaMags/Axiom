"""Helpers for extracting structured pieces out of raw MIME-ish messages."""


def split_subject_and_body(msg: str):
    """Recover a (subject, body) pair from a raw MIME-ish message string.

    The header block is separated from the body by a blank line
    (``\\r\\n\\r\\n``). The ``Subject:`` header, if present, is matched
    case-insensitively and its value is stripped of surrounding whitespace.
    If no blank-line separator is found, the whole message is treated as
    the body.
    """
    header_part, _, body_part = msg.partition("\r\n\r\n")
    subject = ""
    for line in header_part.splitlines():
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
    body = body_part if body_part else msg
    return subject, body

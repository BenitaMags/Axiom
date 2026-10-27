"""Helpers for coercing email payload values into plain strings.

Extracted from ``notifier`` because the coercion logic is a small, reusable,
self-contained piece of behaviour independent of the SMTP/apprise glue.
"""


def coerce_email_body_to_str(payload) -> str:
    """Coerce an ``email`` payload value into a plain ``str``.

    ``Message.get_payload(decode=False)`` can return a plain ``str``, but for
    multipart or oddly-encoded messages it can return an ``email.message.Message``
    instance or a list of such parts. This flattens any such result down to
    plain text.
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        return "".join(coerce_email_body_to_str(item) for item in payload)
    # email.message.Message (or similar) -- render it back out to text.
    return str(payload)

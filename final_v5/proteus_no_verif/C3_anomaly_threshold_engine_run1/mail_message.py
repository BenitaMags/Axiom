"""Value object for the raw SMTP-style message text built by EmailNotifier."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class EmailMessage:
    sender: str
    recipients: List[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""

    def render(self) -> str:
        """Render this message as the raw string handed to smtp.sendmail()."""
        return (
            f"From: {self.sender}\r\n"
            f"To: {', '.join(self.recipients)}\r\n"
            f"Subject: {self.subject}\r\n\r\n{self.body}"
        )

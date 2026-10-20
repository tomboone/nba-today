"""Resend's HTTP API, the primary transport."""

from datetime import UTC, datetime

import resend
from resend.exceptions import NoContentError, ResendError

from nba_today.transport.base import Message, SendReceipt, TransportError


class ResendTransport:
    name = "resend"

    def __init__(self, api_key: str, sender: str) -> None:
        self._api_key = api_key
        self._sender = sender

    def send(self, msg: Message) -> SendReceipt:
        """Send `msg` through Resend, from the configured sender rather than `msg.from_`.

        Resend only sends from a verified domain, so the sender is configuration, as it is for
        SMTP. The SDK reads its key from a module global, so it is set on every send.
        """
        resend.api_key = self._api_key
        params: resend.Emails.SendParams = {
            "from": self._sender,
            "to": msg.to,
            "subject": msg.subject,
            "html": msg.html,
            "text": msg.text,
        }
        try:
            response = resend.Emails.send(params)
        except (ResendError, NoContentError) as exc:
            raise TransportError(f"Resend send failed: {exc}") from exc
        # A 2xx means Resend accepted the mail, so a missing id still counts as a delivery.
        return SendReceipt(
            transport=self.name, message_id=response.get("id"), sent_at=datetime.now(UTC)
        )

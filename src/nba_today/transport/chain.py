"""Try transports in order; the first to deliver wins (spec §7)."""

import logging
from collections.abc import Sequence

from nba_today.transport.base import (
    AllTransportsFailed,
    Message,
    SendReceipt,
    Transport,
    TransportError,
)

log = logging.getLogger(__name__)


class ChainTransport:
    name = "chain"

    def __init__(self, transports: Sequence[Transport]) -> None:
        if not transports:
            raise ValueError("a chain needs at least one transport")
        self.transports = list(transports)

    def send(self, msg: Message) -> SendReceipt:
        """The first receipt any transport returns; raises `AllTransportsFailed` if none does.

        Only `TransportError` moves on to the next transport. Anything else is a bug, not a
        delivery failure, and propagates.
        """
        errors: list[tuple[str, Exception]] = []
        for transport in self.transports:
            try:
                return transport.send(msg)
            except TransportError as exc:
                log.warning(
                    "transport failed", extra={"transport": transport.name, "error": str(exc)}
                )
                errors.append((transport.name, exc))
        raise AllTransportsFailed(errors)

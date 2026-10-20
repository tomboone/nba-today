"""Settings → the transport `send` uses: the dry-run outbox, or the chain in `TRANSPORTS` order."""

import logging
from collections.abc import Callable
from datetime import date

from nba_today.settings import Settings, TransportName
from nba_today.transport.base import Transport
from nba_today.transport.chain import ChainTransport
from nba_today.transport.outbox import OutboxTransport
from nba_today.transport.resend import ResendTransport
from nba_today.transport.smtp import SmtpTransport

log = logging.getLogger(__name__)


class TransportConfigError(Exception):
    """No transport in `TRANSPORTS` is configured, so nothing could ever be sent."""


def _resend(settings: Settings) -> Transport | None:
    if settings.resend_api_key is None or settings.resend_from is None:
        return None
    return ResendTransport(settings.resend_api_key.get_secret_value(), settings.resend_from)


def _smtp(settings: Settings) -> Transport | None:
    if (
        settings.smtp_username is None
        or settings.smtp_password is None
        or settings.smtp_sender_address is None
    ):
        return None
    return SmtpTransport(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_username,
        settings.smtp_password.get_secret_value(),
        settings.smtp_sender_address,
    )


# Each builder returns None when its settings are incomplete.
_BUILDERS: dict[TransportName, Callable[[Settings], Transport | None]] = {
    "resend": _resend,
    "smtp": _smtp,
}


def build_transport(settings: Settings, *, day: date | None = None) -> Transport:
    """`DRY_RUN` → the outbox under `DATA_DIR`; otherwise every configured transport, in order.

    `day` names the outbox files for that digest date rather than the send date.

    A transport whose settings are incomplete is skipped with a warning. Raises
    `TransportConfigError` when that leaves the chain empty.
    """
    if settings.dry_run:
        return OutboxTransport(settings.data_dir / "outbox", tz=settings.tz_local, day=day)
    transports: list[Transport] = []
    for name in settings.transports:
        if (transport := _BUILDERS[name](settings)) is None:
            log.warning("transport not configured, skipping", extra={"transport": name})
        else:
            transports.append(transport)
    if not transports:
        raise TransportConfigError(
            f"no transport is configured (TRANSPORTS={','.join(settings.transports)})"
        )
    return ChainTransport(transports)

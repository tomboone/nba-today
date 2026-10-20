import logging
from datetime import date
from pathlib import Path

import pytest

from nba_today.settings import Settings, TransportName
from nba_today.transport.base import Message
from nba_today.transport.chain import ChainTransport
from nba_today.transport.factory import TransportConfigError, build_transport
from nba_today.transport.outbox import OutboxTransport
from nba_today.transport.resend import ResendTransport
from nba_today.transport.smtp import SmtpTransport


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def _settings(tmp_path: Path, transports: list[TransportName], **overrides: object) -> Settings:
    smtp: dict[str, object] = {
        "smtp_username": "user",
        "smtp_password": "secret",
        "smtp_sender_address": "nba@example.com",
    }
    return Settings.model_validate(
        {
            "recipients": ["fan@example.com"],
            "data_dir": tmp_path,
            "transports": transports,
            **smtp,
            **overrides,
        }
    )


def test_dry_run_is_the_outbox_regardless_of_transports(tmp_path: Path) -> None:
    transport = build_transport(_settings(tmp_path, ["resend", "smtp"], dry_run=True))

    assert isinstance(transport, OutboxTransport)
    assert transport.directory == tmp_path / "outbox"


def test_configured_smtp_builds_a_chain(tmp_path: Path) -> None:
    transport = build_transport(_settings(tmp_path, ["smtp"]))

    assert isinstance(transport, ChainTransport)
    [smtp] = transport.transports
    assert isinstance(smtp, SmtpTransport)


@pytest.mark.parametrize("order", [["resend", "smtp"], ["smtp", "resend"]])
def test_configured_transports_chain_in_transports_order(
    tmp_path: Path, order: list[TransportName]
) -> None:
    settings = _settings(tmp_path, order, resend_api_key="key", resend_from="nba@example.com")

    transport = build_transport(settings)

    assert isinstance(transport, ChainTransport)
    assert [t.name for t in transport.transports] == order
    assert {type(t) for t in transport.transports} == {ResendTransport, SmtpTransport}


def test_resend_without_a_sender_is_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path, ["resend", "smtp"], resend_api_key="key")

    with caplog.at_level(logging.WARNING, logger="nba_today.transport.factory"):
        transport = build_transport(settings)

    assert isinstance(transport, ChainTransport)
    assert [t.name for t in transport.transports] == ["smtp"]
    [record] = caplog.records
    assert record.getMessage() == "transport not configured, skipping"
    assert vars(record)["transport"] == "resend"


@pytest.mark.parametrize("missing", ["smtp_username", "smtp_password", "smtp_sender_address"])
def test_incomplete_smtp_settings_skip_smtp_with_a_warning(
    tmp_path: Path, missing: str, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        caplog.at_level(logging.WARNING, logger="nba_today.transport.factory"),
        pytest.raises(TransportConfigError, match="no transport"),
    ):
        build_transport(_settings(tmp_path, ["smtp"], **{missing: None}))

    [record] = caplog.records
    assert record.getMessage() == "transport not configured, skipping"
    assert vars(record)["transport"] == "smtp"


def test_dry_run_day_names_the_outbox_files(tmp_path: Path) -> None:
    message = Message(subject="s", html="<p>h</p>", text="t", to=["fan@example.com"], from_="")
    transport = build_transport(_settings(tmp_path, ["smtp"], dry_run=True), day=date(2026, 6, 15))

    assert isinstance(transport, OutboxTransport)
    assert transport.send(message).message_id == str(tmp_path / "outbox" / "2026-06-15.html")

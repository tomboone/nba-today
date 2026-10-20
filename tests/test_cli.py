import re

import pytest
from typer.testing import CliRunner

from nba_today.cli import app
from nba_today.settings import Settings

runner = CliRunner()

COMMANDS = ["run", "send", "preview", "health"]
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean environment with only the required recipients set."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("RECIPIENTS", "fan@example.com")


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    # Typer forces Rich styling under GITHUB_ACTIONS; strip it so word boundaries match.
    output = ANSI_ESCAPE.sub("", result.output)
    for command in COMMANDS:
        assert re.search(rf"\b{command}\b", output), command


@pytest.mark.parametrize("command", ["run", "send", "preview"])
def test_invalid_config_exits_2(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.delenv("RECIPIENTS", raising=False)

    result = runner.invoke(app, [command])

    assert result.exit_code == 2
    assert "invalid configuration" in result.output


def test_health_exits_1_not_2_on_invalid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # Docker reserves health-check exit code 2.
    monkeypatch.delenv("RECIPIENTS", raising=False)

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "invalid configuration" in result.output


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.usefixtures("env")
def test_stub_exits_1(command: str) -> None:
    result = runner.invoke(app, [command])

    assert result.exit_code == 1
    assert '"msg": "command not implemented yet"' in result.output

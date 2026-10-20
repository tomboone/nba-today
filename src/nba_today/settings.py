"""All runtime configuration, read from the environment (spec §8)."""

from datetime import time
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

type TransportName = Literal["resend", "smtp"]
type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    tz_local: ZoneInfo = ZoneInfo("America/New_York")
    plan_time: time = time(3, 0)
    send_lead_minutes: int = 30
    scores_only_time: time = time(9, 0)
    data_dir: Path = Path("/data")

    # Required; `default=...` only tells pyright that env, not __init__, supplies it.
    recipients: Annotated[list[str], NoDecode] = Field(default=..., min_length=1)

    resend_api_key: SecretStr | None = None
    resend_from: str | None = None

    smtp_host: str = "smtp.fastmail.com"
    smtp_port: int = 465
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_sender_address: str | None = None

    transports: Annotated[list[TransportName], NoDecode] = ["resend", "smtp"]

    healthchecks_url: str | None = None
    dry_run: bool = False
    log_level: LogLevel = "INFO"

    # Kill switch for every NBA.com fetch; off treats them all as unavailable (ADR-0004).
    nba_enrichment: bool = True
    espn_site_api_base: str = "https://site.api.espn.com"
    espn_web_api_base: str = "https://site.web.api.espn.com"
    nba_cdn_base: str = "https://cdn.nba.com"
    nba_stats_base: str = "https://stats.nba.com"

    @field_validator("recipients", "transports", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

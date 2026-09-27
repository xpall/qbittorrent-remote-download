"""Configuration loading for the qBittorrent remote download app."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MediaType = Literal["movies", "shows"]

DEFAULT_CONFIG_FILENAME = "config.json"
CONFIG_ENV_VAR = "QBT_REMOTE_CONFIG"

DEFAULT_LIBRARIES: dict[str, str] = {
    "movies": "~/Videos/qBittorrent/Movies",
    "shows": "~/Videos/qBittorrent/Shows",
}


class QbittorrentConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8080"
    username: str = "admin"
    password: str = ""
    # Optional: a WebUI API key (qBittorrent 5.2+). When set, it is used
    # instead of the username/password login flow.
    api_key: str = ""
    request_timeout: float = 30.0

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.strip().rstrip("/")


class AppConfig(BaseModel):
    bind_host: str = "127.0.0.1"
    port: int = 8765
    qbittorrent: QbittorrentConfig = Field(default_factory=QbittorrentConfig)
    libraries: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_LIBRARIES))
    tag: str = "remote-dl"
    max_batch: int = 10
    poll_interval: float = 5.0
    state_file: str = "state.json"
    mock: bool = False

    def library_path(self, media_type: str) -> Path:
        """Absolute path of the library folder for a media type."""
        raw = self.libraries.get(media_type)
        if not raw:
            raise KeyError(f"No library configured for media type {media_type!r}")
        return Path(raw).expanduser()

    def validate_libraries(self) -> list[str]:
        """Return human readable problems with the configured libraries."""
        problems: list[str] = []
        for media_type, raw in self.libraries.items():
            path = Path(raw).expanduser()
            if not path.is_absolute():
                problems.append(f"Library path for {media_type!r} is not absolute: {raw}")
            elif not path.exists():
                problems.append(f"Library path for {media_type!r} does not exist: {path}")
            elif not path.is_dir():
                problems.append(f"Library path for {media_type!r} is not a directory: {path}")
        return problems


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load config from *path*, $QBT_REMOTE_CONFIG or ./config.json.

    Falls back to built-in defaults when no file is found.
    """
    candidate: Path | None = None
    if path is not None:
        candidate = Path(path)
    elif env_path := os.environ.get(CONFIG_ENV_VAR):
        candidate = Path(env_path)
    else:
        candidate = Path.cwd() / DEFAULT_CONFIG_FILENAME

    if candidate is not None and candidate.is_file():
        data = json.loads(candidate.read_text(encoding="utf-8"))
        return AppConfig.model_validate(data)

    if path is not None or os.environ.get(CONFIG_ENV_VAR):
        raise FileNotFoundError(f"Config file not found: {candidate}")

    return AppConfig()

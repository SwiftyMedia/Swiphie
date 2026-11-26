"""Runtime configuration for Swiphie.

Everything is environment driven so the same code runs locally and in CI.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VIDEO_IDS = (
    "dQw4w9WgXcQ",
    "9bZkp7q19f0",
    "IgxYypxZDTQ",
    "kJQP7kiw5Fk",
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) "
    "Gecko/20100101 Firefox/140.0"
)

PLAYER_HASH_RE = re.compile(r"^[a-f0-9]{8}$")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class ConfigError(ValueError):
    """Raised when the environment contains an invalid Swiphie setting."""


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else None


def _env_any(*names: str) -> str | None:
    for name in names:
        value = _env(name)
        if value is not None:
            return value
    return None


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return parsed


def _float_env(name: str, default: float, *, minimum: float = 0.1) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return parsed


def _list_env(*names: str) -> list[str] | None:
    value = _env_any(*names)
    if value is None:
        return None
    return [item for item in re.split(r"[\s,]+", value.strip()) if item]


@dataclass(frozen=True)
class Config:
    """Resolved Swiphie configuration."""

    fetch_timeout: float = 10.0
    retry_timeout: float = 15.0
    max_watch_bytes: int = 2_000_000
    max_player_js_bytes: int = 4_000_000
    warmup_video_id: str = DEFAULT_VIDEO_IDS[0]
    validation_video_ids: tuple[str, ...] = DEFAULT_VIDEO_IDS
    player_registry_path: Path = Path("registry/player_registry.json")
    player_configs_path: Path = Path("registry/player_configs.json")
    releases_dir: Path = Path("registry/releases")
    extra_player_hashes: tuple[str, ...] = ()
    discovery_samples: int = 3
    node_binary: str = "node"
    runtime_timeout: float = 15.0
    validation_deadline: float = 120.0
    max_candidate_pairs: int = 24
    client_version: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    web_ua: str = DEFAULT_WEB_UA
    cookie_file: Path | None = None
    discord_webhook: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        """Build a validated configuration from the process environment."""

        video_ids = tuple(
            dict.fromkeys(
                _list_env(
                    "SWIPHIE_VALIDATION_VIDEO_IDS",
                    "VALIDATION_VIDEO_IDS",
                )
                or DEFAULT_VIDEO_IDS
            )
        )
        if len(video_ids) < 2:
            raise ConfigError(
                "SWIPHIE_VALIDATION_VIDEO_IDS must contain at least two distinct video IDs"
            )
        invalid_video = next(
            (video_id for video_id in video_ids if not VIDEO_ID_RE.fullmatch(video_id)),
            None,
        )
        if invalid_video is not None:
            raise ConfigError(
                f"SWIPHIE_VALIDATION_VIDEO_IDS contains an invalid video ID: {invalid_video}"
            )

        hashes = tuple(
            dict.fromkeys(
                _list_env("SWIPHIE_PLAYER_HASHES", "PLAYER_HASHES") or ()
            )
        )
        invalid_hash = next(
            (player_hash for player_hash in hashes if not PLAYER_HASH_RE.fullmatch(player_hash)),
            None,
        )
        if invalid_hash is not None:
            raise ConfigError(
                f"SWIPHIE_PLAYER_HASHES contains an invalid player hash: {invalid_hash}"
            )

        cookie_value = _env_any("SWIPHIE_COOKIE_FILE", "COOKIE_FILE")
        config = cls(
            fetch_timeout=_float_env("SWIPHIE_FETCH_TIMEOUT", 10.0),
            retry_timeout=_float_env("SWIPHIE_RETRY_TIMEOUT", 15.0),
            max_watch_bytes=_int_env("SWIPHIE_MAX_WATCH_BYTES", 2_000_000),
            max_player_js_bytes=_int_env("SWIPHIE_MAX_PLAYER_JS_BYTES", 4_000_000),
            warmup_video_id=_env("SWIPHIE_WARMUP_VIDEO_ID") or DEFAULT_VIDEO_IDS[0],
            validation_video_ids=video_ids,
            player_registry_path=Path(
                _env("SWIPHIE_PLAYER_REGISTRY_PATH")
                or "registry/player_registry.json"
            ),
            player_configs_path=Path(
                _env("SWIPHIE_PLAYER_CONFIGS_PATH")
                or "registry/player_configs.json"
            ),
            releases_dir=Path(
                _env("SWIPHIE_RELEASES_DIR") or "registry/releases"
            ),
            extra_player_hashes=hashes,
            discovery_samples=_int_env("SWIPHIE_DISCOVERY_SAMPLES", 3),
            node_binary=_env("SWIPHIE_NODE") or "node",
            runtime_timeout=_float_env("SWIPHIE_RUNTIME_TIMEOUT", 15.0),
            validation_deadline=_float_env("SWIPHIE_VALIDATION_DEADLINE", 120.0),
            max_candidate_pairs=_int_env("SWIPHIE_MAX_CANDIDATE_PAIRS", 24),
            client_version=_env("SWIPHIE_CLIENT_VERSION") or "",
            user_agent=_env("SWIPHIE_USER_AGENT") or DEFAULT_USER_AGENT,
            web_ua=_env("SWIPHIE_WEB_UA") or DEFAULT_WEB_UA,
            cookie_file=Path(cookie_value) if cookie_value else None,
            discord_webhook=_env("SWIPHIE_DISCORD_WEBHOOK"),
        )
        if not VIDEO_ID_RE.fullmatch(config.warmup_video_id):
            raise ConfigError(
                f"SWIPHIE_WARMUP_VIDEO_ID is not a YouTube video ID: {config.warmup_video_id}"
            )
        return config

    def as_public_dict(self) -> dict[str, object]:
        """Return a JSON-friendly view without secrets."""

        return {
            "fetchTimeout": self.fetch_timeout,
            "retryTimeout": self.retry_timeout,
            "maxWatchBytes": self.max_watch_bytes,
            "maxPlayerJsBytes": self.max_player_js_bytes,
            "warmupVideoId": self.warmup_video_id,
            "validationVideoIds": list(self.validation_video_ids),
            "playerRegistryPath": str(self.player_registry_path),
            "playerConfigsPath": str(self.player_configs_path),
            "releasesDir": str(self.releases_dir),
            "extraPlayerHashes": list(self.extra_player_hashes),
            "discoverySamples": self.discovery_samples,
            "nodeBinary": self.node_binary,
            "runtimeTimeout": self.runtime_timeout,
            "validationDeadline": self.validation_deadline,
            "maxCandidatePairs": self.max_candidate_pairs,
            "clientVersion": self.client_version or "auto",
            "cookieFile": str(self.cookie_file) if self.cookie_file else None,
            "discordWebhookConfigured": bool(self.discord_webhook),
        }

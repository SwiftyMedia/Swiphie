"""Load player JS from a file, a URL or a discovered hash."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import Config
from .discovery import (
    candidate_for_hash,
    discover_current_player_url,
    player_url_for_hash,
)
from .hash import sha256_hex
from .http import HttpError, fetch_text
from .registry import extract_player_hash

PLAYER_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.youtube.com/",
}


@dataclass(frozen=True)
class PlayerSource:
    player_hash: str
    player_url: str | None
    player_code: str
    sha256: str


def load_player_source(
    config: Config,
    *,
    player_file: Path | None = None,
    player_url: str | None = None,
    player_hash: str | None = None,
) -> PlayerSource:
    """Resolve one player source, fetching it when necessary."""

    # Local files make derivation reproducible and avoid a player download
    if player_file is not None:
        code = player_file.read_text(encoding="utf-8")
        _check_size(code, config.max_player_js_bytes, str(player_file))
        digest = sha256_hex(code)
        resolved_hash = player_hash
        if resolved_hash is None and player_url:
            resolved_hash = extract_player_hash(player_url)
        if resolved_hash is None:
            resolved_hash = digest[:8]
        return PlayerSource(
            player_hash=resolved_hash,
            player_url=player_url,
            player_code=code,
            sha256=digest,
        )

    if player_url is None:
        if player_hash is not None:
            player_url = player_url_for_hash(player_hash)
        else:
            player_url = discover_current_player_url(config)

    player_url = normalize_player_url(player_url, cipher_mode=True)
    code = fetch_player_js(player_url, config)
    digest = sha256_hex(code)
    resolved_hash = player_hash or extract_player_hash(player_url)
    return PlayerSource(
        player_hash=resolved_hash,
        player_url=player_url,
        player_code=code,
        sha256=digest,
    )


def fetch_player_js(url: str, config: Config) -> str:
    try:
        return fetch_text(
            url,
            headers={"User-Agent": config.user_agent, **PLAYER_HEADERS},
            timeout=config.fetch_timeout,
            max_bytes=config.max_player_js_bytes,
        )
    except HttpError as exc:
        raise RuntimeError(f"could not fetch player JS: {exc}") from exc


def normalize_player_url(url: str, *, cipher_mode: bool = True) -> str:
    """Switch es6/en_US URLs to the ias/en_GB form used for derivation."""

    if not cipher_mode:
        return url
    parts = urlsplit(url)
    path = parts.path.replace("/player_es6.", "/player_ias.").replace(
        "/en_US/",
        "/en_GB/",
    )
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def candidate_url_for_hash(player_hash: str) -> str:
    return candidate_for_hash(player_hash, "configured").player_url


def _check_size(code: str, max_bytes: int, label: str) -> None:
    size = len(code.encode("utf-8"))
    if size > max_bytes:
        raise RuntimeError(f"{label} exceeded {max_bytes} bytes ({size})")

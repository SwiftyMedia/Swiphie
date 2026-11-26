"""Discover current and near-current YouTube player candidates.

YouTube rotates ``player_ias`` frequently.  The iframe API is the most
reliable single source; music/watch/embed pages are fallbacks.  Candidates are
sampled more than once and ranked by how often they appear.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin

from .config import Config
from .http import HttpError, fetch_text
from .registry import extract_player_hash

IFRAME_API_URL = "https://www.youtube.com/iframe_api"

PLAYER_URL_PATTERN = re.compile(
    r"""(?:https?:)?//www\.youtube\.com/s/player/[^"'\\]+\/base\.js|/s/player/[^"'\\]+\/base\.js"""
)
IFRAME_HASH_PATTERN = re.compile(r"\\?/s\\?/player\\?/([a-f0-9]{8})\\?/")

WATCH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class PlayerCandidate:
    player_hash: str
    player_url: str
    source: str


def discover_player_candidates(config: Config) -> list[PlayerCandidate]:
    """Return ranked player candidates, raising if discovery finds none."""

    # Sample sources concurrently so one slow page does not block discovery
    samples = max(1, config.discovery_samples)
    with ThreadPoolExecutor(max_workers=4) as pool:
        iframe_future = pool.submit(
            _sample_candidates,
            samples,
            lambda: _discover_from_iframe_api(config),
        )
        music_future = pool.submit(
            _sample_candidates,
            max(1, samples - 1),
            lambda: _discover_from_page(
                "https://music.youtube.com/",
                "music-page",
                config,
            ),
        )
        watch_future = pool.submit(
            _sample_candidates,
            max(1, samples - 1),
            lambda: _discover_from_watch_page(config),
        )
        embed_future = pool.submit(
            _sample_candidates,
            max(1, samples - 1),
            lambda: _discover_from_embed_page(config),
        )

        candidates: list[PlayerCandidate] = []
        errors: list[str] = []
        for name, future in (
            ("iframe-api", iframe_future),
            ("music-page", music_future),
            ("watch-page", watch_future),
            ("embed-page", embed_future),
        ):
            try:
                candidates.extend(future.result())
            except Exception as exc:  # noqa: BLE001 - discovery is best effort
                errors.append(f"{name}: {exc}")

    configured = [
        candidate_for_hash(player_hash, "configured")
        for player_hash in config.extra_player_hashes
    ]
    candidates.extend(configured)

    deduped = _dedupe(candidates)
    if not deduped:
        detail = f" ({'; '.join(errors)})" if errors else ""
        raise RuntimeError(f"no supported player hashes discovered{detail}")
    return deduped


def discover_current_player_url(config: Config) -> str:
    candidates = discover_player_candidates(config)
    return candidates[0].player_url


def candidate_for_hash(player_hash: str, source: str) -> PlayerCandidate:
    if not re.fullmatch(r"[a-f0-9]{8}", player_hash):
        raise ValueError(f"unsupported player hash {player_hash!r} from {source}")
    return PlayerCandidate(
        player_hash=player_hash,
        player_url=player_url_for_hash(player_hash),
        source=source,
    )


def player_url_for_hash(player_hash: str, *, cipher_mode: bool = True) -> str:
    kind = "player_ias" if cipher_mode else "player_es6"
    locale = "en_GB" if cipher_mode else "en_US"
    return (
        f"https://www.youtube.com/s/player/{player_hash}/"
        f"{kind}.vflset/{locale}/base.js"
    )


def _discover_from_iframe_api(config: Config) -> list[PlayerCandidate]:
    text = _fetch_text(IFRAME_API_URL, config)
    hashes = IFRAME_HASH_PATTERN.findall(text)
    if not hashes:
        raise RuntimeError("could not find player hash in iframe_api")
    return [candidate_for_hash(value, "iframe-api") for value in hashes]


def _discover_from_watch_page(config: Config) -> list[PlayerCandidate]:
    url = (
        "https://www.youtube.com/watch?v="
        f"{config.warmup_video_id}&hl=en&bpctr=9999999999&has_verified=1"
    )
    return _discover_from_page(url, "watch-page", config)


def _discover_from_embed_page(config: Config) -> list[PlayerCandidate]:
    url = f"https://www.youtube.com/embed/{config.warmup_video_id}"
    return _discover_from_page(url, "embed-page", config)


def _discover_from_page(url: str, source: str, config: Config) -> list[PlayerCandidate]:
    html = _fetch_text(url, config)
    values = PLAYER_URL_PATTERN.findall(html)
    if not values:
        raise RuntimeError(f"could not find player JS URL in {source}")

    candidates: list[PlayerCandidate] = []
    for value in values:
        absolute = urljoin("https://www.youtube.com", value.replace("\\/", "/"))
        player_hash = extract_player_hash(absolute)
        candidates.append(candidate_for_hash(player_hash, source))
    return candidates


def _fetch_text(url: str, config: Config) -> str:
    try:
        return fetch_text(
            url,
            headers={"User-Agent": config.user_agent, **WATCH_HEADERS},
            timeout=config.fetch_timeout,
            max_bytes=config.max_watch_bytes,
        )
    except HttpError:
        raise


def _sample_candidates(
    count: int,
    sample: Callable[[], list[PlayerCandidate]],
) -> list[PlayerCandidate]:
    candidates: list[PlayerCandidate] = []
    last_error: Exception | None = None
    for _ in range(count):
        try:
            candidates.extend(sample())
        except Exception as exc:  # noqa: BLE001 - keep sampling
            last_error = exc
    if not candidates and last_error is not None:
        raise last_error

    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.player_hash] = counts.get(candidate.player_hash, 0) + 1
    return sorted(
        candidates,
        key=lambda candidate: counts.get(candidate.player_hash, 0),
        reverse=True,
    )


def _dedupe(candidates: list[PlayerCandidate]) -> list[PlayerCandidate]:
    seen: set[str] = set()
    output: list[PlayerCandidate] = []
    for candidate in candidates:
        if candidate.player_hash in seen:
            continue
        seen.add(candidate.player_hash)
        output.append(candidate)
    return output

"""Registry metadata: discovered players, release records and validation.

``player_configs.json`` is the public config file. This registry adds
the provenance that makes the repository auditable: which player URL was
seen, its SHA-256, when it was first discovered and which release record
belongs to it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .parser import PLAYER_HASH_RE, write_text_atomic

VALIDATOR = "swiphie"
ALLOWED_VALIDATORS = {"swiphie", "imported"}
PLAYER_URL_HASH_RE = re.compile(r"/s/player/([a-f0-9]{8})/")


@dataclass
class PlayerRegistryEntry:
    player_hash: str
    player_url: str
    sha256: str
    first_seen_at: str
    status: str
    validator: str
    config_path: str
    release_tag: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "playerHash": self.player_hash,
            "playerUrl": self.player_url,
            "sha256": self.sha256,
            "firstSeenAt": self.first_seen_at,
            "status": self.status,
            "validator": self.validator,
            "configPath": self.config_path,
        }
        if self.release_tag:
            data["releaseTag"] = self.release_tag
        return data


@dataclass
class PlayerRegistry:
    schema_version: int = 1
    updated_at: str = ""
    current: dict[str, str] | None = None
    players: list[PlayerRegistryEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "updatedAt": self.updated_at,
            "current": self.current,
            "players": [player.to_dict() for player in self.players],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"


def iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def empty_registry() -> PlayerRegistry:
    return PlayerRegistry(
        schema_version=1,
        updated_at=iso_now(),
        current=None,
        players=[],
    )


def read_player_registry(path: Path) -> PlayerRegistry | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid player registry {path}: malformed JSON: {exc}") from exc
    return validate_player_registry(value, str(path))


def validate_player_registry(
    value: object,
    label: str = "player registry",
) -> PlayerRegistry:
    if not _is_record(value):
        raise ValueError(f"invalid {label}: root is not an object")
    if value.get("schemaVersion") != 1:
        raise ValueError(f"invalid {label}: unsupported schemaVersion")
    if not is_iso_date(value.get("updatedAt")):
        raise ValueError(f"invalid {label}: updatedAt is not an ISO timestamp")
    raw_players = value.get("players")
    if not isinstance(raw_players, list):
        raise ValueError(f"invalid {label}: players is not an array")

    players: list[PlayerRegistryEntry] = []
    hashes: set[str] = set()
    for index, raw in enumerate(raw_players):
        if not _is_record(raw):
            raise ValueError(f"invalid {label}: players[{index}] is not an object")
        player_hash = _required_hash(
            raw.get("playerHash"),
            f"{label} players[{index}]",
        )
        if player_hash in hashes:
            raise ValueError(f"invalid {label}: duplicate player {player_hash}")
        hashes.add(player_hash)
        player_url = _required_player_url(
            raw.get("playerUrl"),
            player_hash,
            f"{label} players[{index}]",
        )
        sha256 = raw.get("sha256")
        if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError(f"invalid {label}: bad sha256 for {player_hash}")
        first_seen_at = _required_iso_date(
            raw.get("firstSeenAt"),
            f"{label} players[{index}] firstSeenAt",
        )
        status = raw.get("status")
        if status not in {"validated", "needs-derivation"}:
            raise ValueError(f"invalid {label}: bad status for {player_hash}")
        validator = raw.get("validator")
        if validator not in ALLOWED_VALIDATORS:
            raise ValueError(f"invalid {label}: bad validator for {player_hash}")
        config_path = raw.get("configPath")
        if not isinstance(config_path, str) or not config_path:
            raise ValueError(f"invalid {label}: bad configPath for {player_hash}")
        release_tag = raw.get("releaseTag")
        if release_tag is not None and release_tag != f"player-{player_hash}":
            raise ValueError(f"invalid {label}: bad releaseTag for {player_hash}")
        players.append(
            PlayerRegistryEntry(
                player_hash=player_hash,
                player_url=player_url,
                sha256=sha256,
                first_seen_at=first_seen_at,
                status=status,
                validator=validator,
                config_path=config_path,
                release_tag=release_tag,
            )
        )

    current_value = value.get("current")
    current: dict[str, str] | None = None
    if current_value is not None:
        if not _is_record(current_value):
            raise ValueError(f"invalid {label}: current is not an object or null")
        current_hash = _required_hash(
            current_value.get("playerHash"),
            f"{label} current",
        )
        if current_hash not in hashes:
            raise ValueError(
                f"invalid {label}: current player {current_hash} is not in players"
            )
        current = {
            "playerHash": current_hash,
            "playerUrl": _required_player_url(
                current_value.get("playerUrl"),
                current_hash,
                f"{label} current",
            ),
            "discoveredAt": _required_iso_date(
                current_value.get("discoveredAt"),
                f"{label} current discoveredAt",
            ),
        }

    return PlayerRegistry(
        schema_version=1,
        updated_at=str(value["updatedAt"]),
        current=current,
        players=players,
    )


def write_registry_atomic(path: Path, registry: PlayerRegistry) -> None:
    # Validate before replacing the last known-good registry
    validate_player_registry(registry.to_dict(), str(path))
    write_text_atomic(path, registry.to_json())


def extract_player_hash(player_url: str) -> str:
    match = PLAYER_URL_HASH_RE.search(player_url)
    if not match:
        raise ValueError(f"unsupported player hash in {player_url}")
    return match.group(1)


def release_path_for(releases_dir: Path, player_hash: str) -> Path:
    return releases_dir / f"{player_hash}.json"


def write_release_record(
    releases_dir: Path,
    player_hash: str,
    player_url: str,
    sha256: str,
    generated_at: str | None = None,
) -> Path:
    path = release_path_for(releases_dir, player_hash)
    record = {
        "schemaVersion": 1,
        "generatedAt": generated_at or iso_now(),
        "playerHash": player_hash,
        "playerUrl": player_url,
        "sha256": sha256,
    }
    write_text_atomic(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return path


def update_registry_entry(
    registry: PlayerRegistry,
    player_hash: str,
    player_url: str,
    sha256: str,
    config_path: str,
    *,
    now: str | None = None,
    update_current: bool = True,
) -> PlayerRegistry:
    timestamp = now or iso_now()
    release_tag = f"player-{player_hash}"
    existing = next(
        (player for player in registry.players if player.player_hash == player_hash),
        None,
    )
    if existing is None:
        registry.players.append(
            PlayerRegistryEntry(
                player_hash=player_hash,
                player_url=player_url,
                sha256=sha256,
                first_seen_at=timestamp,
                status="validated",
                validator=VALIDATOR,
                config_path=config_path,
                release_tag=release_tag,
            )
        )
    else:
        existing.player_url = player_url
        existing.sha256 = sha256
        existing.config_path = config_path
        existing.release_tag = release_tag
        existing.status = "validated"
        existing.validator = VALIDATOR

    if update_current:
        registry.current = {
            "playerHash": player_hash,
            "playerUrl": player_url,
            "discoveredAt": timestamp,
        }
    registry.players.sort(key=lambda player: player.player_hash)
    registry.updated_at = timestamp
    return registry


def _required_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not PLAYER_HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {label}: bad player hash")
    return value


def _required_player_url(value: object, player_hash: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid {label}: playerUrl is not a string")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError(f"invalid {label}: malformed playerUrl") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.youtube.com"
        or extract_player_hash(value) != player_hash
    ):
        raise ValueError(f"invalid {label}: playerUrl does not match {player_hash}")
    return value


def _required_iso_date(value: object, label: str) -> str:
    if not is_iso_date(value):
        raise ValueError(f"invalid {label}: not an ISO timestamp")
    return str(value)


def is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_record(value: object) -> bool:
    return isinstance(value, dict)

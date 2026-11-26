"""Artifact verification used by CI and ``swiphie verify``."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .parser import (
    find_committed_entry,
    parse_player_configs,
    read_player_configs_file,
)
from .registry import (
    PlayerRegistry,
    read_player_registry,
    release_path_for,
)


def verify_artifacts(config: Config) -> list[str]:
    """Return a list of human-readable problems; empty means healthy."""

    errors: list[str] = []

    if not config.player_configs_path.exists():
        errors.append(f"missing player configs: {config.player_configs_path}")
    else:
        try:
            text = config.player_configs_path.read_text(encoding="utf-8")
            result = parse_player_configs(text)
            if not result.ok:
                errors.append(
                    f"invalid player configs {config.player_configs_path}: "
                    f"{result.failure()}"
                )
            elif result.skipped:
                errors.append(
                    f"invalid player configs {config.player_configs_path}: "
                    f"skipped entries {', '.join(result.skipped)}"
                )
            else:
                read_player_configs_file(config.player_configs_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid player configs {config.player_configs_path}: {exc}")

    registry: PlayerRegistry | None = None
    if not config.player_registry_path.exists():
        errors.append(f"missing player registry: {config.player_registry_path}")
    else:
        try:
            registry = read_player_registry(config.player_registry_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid player registry {config.player_registry_path}: {exc}")

    # Cross-check provenance, config entries and release records
    if registry is not None and config.player_configs_path.exists():
        try:
            config_file = read_player_configs_file(config.player_configs_path)
        except Exception:
            config_file = None
        if config_file is not None:
            for player in registry.players:
                committed = find_committed_entry(
                    config_file.players,
                    player.player_hash,
                    player.player_hash,
                )
                if committed is None:
                    errors.append(
                        f"registry player {player.player_hash} is missing from "
                        f"{config.player_configs_path}"
                    )
                config_path = Path(player.config_path)
                if not config_path.exists():
                    errors.append(
                        f"registry player {player.player_hash} points at missing "
                        f"config path {config_path}"
                    )
                release_path = release_path_for(
                    config.releases_dir,
                    player.player_hash,
                )
                if not release_path.exists():
                    errors.append(
                        f"registry player {player.player_hash} has no release record "
                        f"at {release_path}"
                    )
                else:
                    errors.extend(_verify_release(release_path, player.player_hash))

    return errors


def _verify_release(path: Path, player_hash: str) -> list[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"invalid release record {path}: {exc}"]
    if not isinstance(value, dict):
        return [f"invalid release record {path}: not an object"]
    if value.get("schemaVersion") != 1:
        return [f"invalid release record {path}: bad schemaVersion"]
    if value.get("playerHash") != player_hash:
        return [f"invalid release record {path}: playerHash mismatch"]
    if not isinstance(value.get("playerUrl"), str):
        return [f"invalid release record {path}: missing playerUrl"]
    if not isinstance(value.get("sha256"), str) or len(value["sha256"]) != 64:
        return [f"invalid release record {path}: bad sha256"]
    return []

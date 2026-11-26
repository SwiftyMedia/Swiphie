"""End-to-end player probe.

The probe is fail-closed: a config is only written after two distinct
signature ciphers pass strict HTTP 206 audio range checks on the live CDN.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from .config import Config
from .credentials import describe_cred, load_innertube_cred
from .deriver import (
    CandidatePair,
    derive_player_config,
    derived_to_entry,
    enumerate_candidate_pairs,
)
from .discovery import (
    PlayerCandidate,
    candidate_for_hash,
    discover_player_candidates,
)
from .parser import (
    PlayerConfigEntry,
    find_committed_entry,
    format_paste_ready_entry,
    load_or_empty_player_configs,
    merge_player_config_entry,
    parse_player_configs,
)
from .player_source import load_player_source, normalize_player_url
from .registry import (
    PlayerRegistry,
    empty_registry,
    extract_player_hash,
    iso_now,
    read_player_registry,
    update_registry_entry,
    write_registry_atomic,
    write_release_record,
)
from .stream import (
    DEFAULT_CLIENT_VERSION,
    StreamSettings,
    fetch_validation_ciphers,
    resolve_client_version,
    validate_candidate_pairs,
)


@dataclass
class ProbeSuccess:
    player_hash: str
    player_url: str | None
    action: str
    stream_mode: str | None = None
    new_player_hashes: list[str] = field(default_factory=list)
    current_player_hash: str | None = None
    details: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "playerHash": self.player_hash,
            "playerUrl": self.player_url,
            "action": self.action,
            "streamMode": self.stream_mode,
        }
        if self.new_player_hashes:
            data["newPlayerHashes"] = self.new_player_hashes
        if self.current_player_hash:
            data["currentPlayerHash"] = self.current_player_hash
        if self.details:
            data["details"] = self.details
        return data


@dataclass
class ProbeFailure:
    stage: str
    player_hash: str
    message: str
    player_url: str | None = None
    details: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "stage": self.stage,
            "playerHash": self.player_hash,
            "message": self.message,
        }
        if self.player_url:
            data["playerUrl"] = self.player_url
        if self.details:
            data["details"] = self.details
        return data


@dataclass
class ProbeResult:
    ok: bool
    success: ProbeSuccess | None = None
    failure: ProbeFailure | None = None

    def to_dict(self) -> dict[str, object]:
        if self.ok and self.success:
            return {"ok": True, "success": self.success.to_dict()}
        return {
            "ok": False,
            "failure": self.failure.to_dict() if self.failure else None,
        }


def run_player_probe(
    config: Config,
    *,
    check_only: bool = False,
    player_hash: str | None = None,
    player_url: str | None = None,
    player_file: Path | None = None,
    update_current: bool = True,
    dry_run: bool = False,
) -> ProbeResult:
    """Run one probe cycle."""

    settings = StreamSettings.from_config(config)
    if not config.client_version:
        settings = replace(
            settings,
            client_version=resolve_client_version(config),
        )
    now = iso_now()

    # 1. Resolve explicit sources or discover candidates.
    source = None
    candidates: list[PlayerCandidate] = []
    candidate: PlayerCandidate | None = None
    try:
        if player_file is not None:
            source = load_player_source(
                config,
                player_file=player_file,
                player_url=player_url,
                player_hash=player_hash,
            )
            candidate = PlayerCandidate(
                player_hash=source.player_hash,
                player_url=(
                    normalize_player_url(source.player_url, cipher_mode=True)
                    if source.player_url
                    else ""
                ),
                source="file",
            )
        elif player_hash is not None:
            candidate = candidate_for_hash(player_hash, "configured")
            candidates = [candidate]
        elif player_url is not None:
            normalized = normalize_player_url(player_url, cipher_mode=True)
            candidate = PlayerCandidate(
                player_hash=extract_player_hash(normalized),
                player_url=normalized,
                source="configured",
            )
            candidates = [candidate]
        else:
            candidates = discover_player_candidates(config)
    except Exception as exc:  # noqa: BLE001 - reported as a probe failure
        return _fail("discovery", player_hash or "unknown", str(exc))

    # 2. Load the existing registry and config table.
    try:
        registry = read_player_registry(config.player_registry_path) or empty_registry()
        config_file = load_or_empty_player_configs(config.player_configs_path)
        config_text = (
            config.player_configs_path.read_text(encoding="utf-8")
            if config.player_configs_path.exists()
            else ""
        )
        parsed = (
            parse_player_configs(config_text)
            if config_text
            else None
        )
        if parsed is not None:
            if not parsed.ok:
                raise ValueError(parsed.failure())
            if parsed.skipped:
                raise ValueError(
                    "skipped entries " + ", ".join(parsed.skipped)
                )
            store = parsed.configs
        else:
            store = {}
    except Exception as exc:  # noqa: BLE001
        return _fail("state", candidate.player_hash if candidate else "unknown", str(exc))

    # 3. Pick a candidate. Prefer one that is not in the table yet.
    if candidate is None:
        candidate = next(
            (
                item
                for item in candidates
                if item.player_hash not in store
            ),
            candidates[0] if candidates else None,
        )
    if candidate is None:
        return _fail("discovery", "unknown", "no player candidate")

    player_hash = candidate.player_hash
    player_url = candidate.player_url or None

    # 4. Already configured: only maintain the "current" pointer.
    if player_hash in store:
        existing = _find_registry_entry(registry, player_hash)
        # A config can exist without registry provenance (e.g. the table was
        # seeded from another project).  Never point ``current`` at a player
        # that is absent from ``players``; that would make the registry invalid.
        needs_current = (
            update_current
            and existing is not None
            and player_url is not None
            and (
                registry.current is None
                or registry.current.get("playerHash") != player_hash
                or registry.current.get("playerUrl") != player_url
            )
        )
        if needs_current and not dry_run:
            if existing is not None:
                existing.player_url = player_url
            registry.current = {
                "playerHash": player_hash,
                "playerUrl": player_url,
                "discoveredAt": now,
            }
            registry.updated_at = now
            try:
                write_registry_atomic(config.player_registry_path, registry)
            except Exception as exc:  # noqa: BLE001
                return _fail(
                    "write",
                    player_hash,
                    str(exc),
                    player_url=player_url,
                )
        return ProbeResult(
            ok=True,
            success=ProbeSuccess(
                player_hash=player_hash,
                player_url=player_url,
                action="current-updated" if needs_current else "unchanged",
                details={"dryRun": dry_run} if dry_run else None,
            ),
        )

    if check_only:
        new_hashes = [
            item.player_hash for item in candidates if item.player_hash not in store
        ]
        return ProbeResult(
            ok=True,
            success=ProbeSuccess(
                player_hash=player_hash,
                player_url=player_url,
                action="new-player",
                new_player_hashes=new_hashes,
                current_player_hash=candidates[0].player_hash if candidates else None,
            ),
        )

    # 5. Fetch the player JS (unless it came from a file).
    if source is None:
        try:
            source = load_player_source(
                config,
                player_url=player_url,
                player_hash=player_hash,
            )
        except Exception as exc:  # noqa: BLE001
            return _fail("fetch", player_hash, str(exc), player_url=player_url)

    player_js = source.player_code
    sha256 = source.sha256

    # 6. Derive cipher metadata.
    derived = derive_player_config(player_js, known_hash=player_hash)
    if not derived.sts:
        return _fail(
            "derivation",
            player_hash,
            "could not extract sts from player.js",
            player_url=player_url,
            details={"derived": derived.to_dict()},
        )

    known_config = store.get(player_hash) or store.get(derived.md5_alias)
    committed = find_committed_entry(
        config_file.players,
        player_hash,
        derived.md5_alias,
    )

    # 7. Fetch two distinct live signature ciphers.
    cred = load_innertube_cred(config)
    try:
        samples = fetch_validation_ciphers(
            derived.sts,
            config.validation_video_ids,
            cred=cred,
            settings=settings,
            prefer_auth=bool(cred.cookie),
        )
    except Exception as first_error:  # noqa: BLE001
        # An auto-discovered client version can occasionally be too new for
        # the player endpoint.  Retry with the pinned fallback before failing.
        if (
            not config.client_version
            and settings.client_version != DEFAULT_CLIENT_VERSION
        ):
            try:
                settings = replace(
                    settings,
                    client_version=DEFAULT_CLIENT_VERSION,
                )
                samples = fetch_validation_ciphers(
                    derived.sts,
                    config.validation_video_ids,
                    cred=cred,
                    settings=settings,
                    prefer_auth=bool(cred.cookie),
                )
            except Exception as second_error:  # noqa: BLE001
                return _fail(
                    "cipher-fetch",
                    player_hash,
                    f"{first_error}; pinned client version also failed: {second_error}",
                    player_url=player_url,
                    details={
                        "sts": derived.sts,
                        "clientVersion": settings.client_version,
                    },
                )
        else:
            return _fail(
                "cipher-fetch",
                player_hash,
                str(first_error),
                player_url=player_url,
                details={
                    "sts": derived.sts,
                    "clientVersion": settings.client_version,
                },
            )
    signature_ciphers = samples.ciphers
    stream_mode = samples.mode

    # 8a. The hash is known through an alias. Re-validate and repair it.
    if known_config is not None:
        validation = validate_candidate_pairs(
            config,
            player_js=player_js,
            player_hash=player_hash,
            md5_alias=derived.md5_alias,
            sts=derived.sts,
            signature_ciphers=signature_ciphers,
            pairs=[CandidatePair(known_config.sig, known_config.n_class)],
            settings=settings,
        )
        if validation.winner is None:
            return _fail(
                "stream-validation",
                player_hash,
                (
                    "committed player config validation was ambiguous"
                    if validation.ambiguous
                    else "committed player config failed strict HTTP 206 stream checks"
                ),
                player_url=player_url,
                details={
                    "sts": derived.sts,
                    "config": known_config.to_dict(),
                    "attempts": [attempt.to_dict() for attempt in validation.attempts],
                    "streamMode": stream_mode,
                    "cred": describe_cred(cred),
                },
            )
        if committed is None:
            return _fail(
                "state",
                player_hash,
                "resolved config is missing from the raw player config table",
                player_url=player_url,
                details={"md5Alias": derived.md5_alias},
            )

        primary_hash, committed_entry = committed
        aliases = _merge_aliases(
            committed_entry.aliases,
            player_hash,
            derived.md5_alias,
            primary_hash,
        )
        repaired = PlayerConfigEntry(
            sig=committed_entry.sig,
            n_class=committed_entry.n_class,
            sts=derived.sts,
            aliases=aliases,
        )
        needs_config_update = repaired != committed_entry
        if needs_config_update and not dry_run:
            try:
                config_file = merge_player_config_entry(
                    config.player_configs_path,
                    primary_hash,
                    repaired,
                )
            except Exception as exc:  # noqa: BLE001
                return _fail(
                    "write",
                    player_hash,
                    str(exc),
                    player_url=player_url,
                    details={
                        "primaryHash": primary_hash,
                        "repairedEntry": repaired.to_dict(),
                    },
                )

        if player_url is None:
            return ProbeResult(
                ok=True,
                success=ProbeSuccess(
                    player_hash=player_hash,
                    player_url=None,
                    action="config-written" if needs_config_update else "unchanged",
                    stream_mode=stream_mode,
                    details={"dryRun": dry_run} if dry_run else None,
                ),
            )

        existing = _find_registry_entry(registry, player_hash)
        needs_registry_update = _registry_needs_update(
            registry,
            existing,
            player_hash=player_hash,
            player_url=player_url,
            sha256=sha256,
            config_path=str(config.player_configs_path),
            update_current=update_current,
            config_changed=needs_config_update,
        )
        if not dry_run and needs_registry_update:
            try:
                _write_registry(
                    config,
                    registry,
                    player_hash=player_hash,
                    player_url=player_url,
                    sha256=sha256,
                    update_current=update_current,
                )
            except Exception as exc:  # noqa: BLE001
                return _fail("write", player_hash, str(exc), player_url=player_url)

        if existing is None:
            action = "stream-validated"
        elif needs_registry_update:
            action = "registry-updated"
        else:
            action = "unchanged"
        return ProbeResult(
            ok=True,
            success=ProbeSuccess(
                player_hash=player_hash,
                player_url=player_url,
                action="dry-run" if dry_run else action,
                stream_mode=stream_mode,
                details={
                    "attempts": [attempt.to_dict() for attempt in validation.attempts],
                    "repaired": needs_config_update,
                },
            ),
        )

    # 8b. Derive a new config, enumerate candidates, validate the winner.
    derived_entry = derived_to_entry(derived, player_hash)
    enumerated = enumerate_candidate_pairs(player_js)
    pairs: list[CandidatePair] = []
    if derived_entry is not None:
        pairs.append(CandidatePair(derived_entry.sig, derived_entry.n_class))
    pairs.extend(enumerated.pairs)
    pairs = _dedupe_pairs(pairs)

    if not pairs:
        return _fail(
            "derivation",
            player_hash,
            "no sig/nClass candidates extracted",
            player_url=player_url,
            details={
                "derived": derived.to_dict(),
                "sigExprs": enumerated.sig_exprs,
                "nClasses": enumerated.n_classes,
            },
        )

    validation = validate_candidate_pairs(
        config,
        player_js=player_js,
        player_hash=player_hash,
        md5_alias=derived.md5_alias,
        sts=derived.sts,
        signature_ciphers=signature_ciphers,
        pairs=pairs,
        settings=settings,
    )
    if validation.winner is None:
        return _fail(
            "stream-validation",
            player_hash,
            (
                "multiple candidate pairs passed strict stream validation"
                if validation.ambiguous
                else "no candidate passed strict HTTP 206 stream validation"
            ),
            player_url=player_url,
            details={
                "derived": derived.to_dict(),
                "derivedEntry": derived_entry.to_dict() if derived_entry else None,
                "attempts": [attempt.to_dict() for attempt in validation.attempts],
                "streamMode": stream_mode,
                "pasteReady": (
                    format_paste_ready_entry(player_hash, derived_entry)
                    if derived_entry
                    else None
                ),
            },
        )

    winner = validation.winner
    if dry_run:
        return ProbeResult(
            ok=True,
            success=ProbeSuccess(
                player_hash=player_hash,
                player_url=player_url,
                action="dry-run",
                stream_mode=stream_mode,
                details={
                    "winner": winner.entry.to_dict(),
                    "attempts": [attempt.to_dict() for attempt in validation.attempts],
                    "pasteReady": format_paste_ready_entry(
                        winner.player_hash,
                        winner.entry,
                    ),
                },
            ),
        )

    try:
        config_file = merge_player_config_entry(
            config.player_configs_path,
            winner.player_hash,
            winner.entry,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "write",
            player_hash,
            str(exc),
            player_url=player_url,
            details={"winner": winner.entry.to_dict()},
        )

    if player_url is None:
        return ProbeResult(
            ok=True,
            success=ProbeSuccess(
                player_hash=player_hash,
                player_url=None,
                action="config-written",
                stream_mode=stream_mode,
                details={"winner": winner.entry.to_dict()},
            ),
        )

    try:
        _write_registry(
            config,
            registry,
            player_hash=player_hash,
            player_url=player_url,
            sha256=sha256,
            update_current=update_current,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail("write", player_hash, str(exc), player_url=player_url)

    return ProbeResult(
        ok=True,
        success=ProbeSuccess(
            player_hash=player_hash,
            player_url=player_url,
            action="derived-and-written",
            stream_mode=stream_mode,
            details={
                "winner": winner.entry.to_dict(),
                "attempts": [attempt.to_dict() for attempt in validation.attempts],
            },
        ),
    )


def _write_registry(
    config: Config,
    registry: PlayerRegistry,
    *,
    player_hash: str,
    player_url: str,
    sha256: str,
    update_current: bool,
) -> None:
    update_registry_entry(
        registry,
        player_hash=player_hash,
        player_url=player_url,
        sha256=sha256,
        config_path=str(config.player_configs_path),
        update_current=update_current,
    )
    write_release_record(
        config.releases_dir,
        player_hash,
        player_url,
        sha256,
    )
    write_registry_atomic(config.player_registry_path, registry)


def _find_registry_entry(
    registry: PlayerRegistry,
    player_hash: str,
):
    return next(
        (player for player in registry.players if player.player_hash == player_hash),
        None,
    )


def _registry_needs_update(
    registry: PlayerRegistry,
    existing,
    *,
    player_hash: str,
    player_url: str,
    sha256: str,
    config_path: str,
    update_current: bool,
    config_changed: bool,
) -> bool:
    if existing is None:
        return True
    if (
        config_changed
        or existing.player_url != player_url
        or existing.sha256 != sha256
        or existing.config_path != config_path
        or existing.release_tag != f"player-{player_hash}"
        or existing.status != "validated"
        or existing.validator != "swiphie"
    ):
        return True
    if update_current:
        current = registry.current
        if (
            current is None
            or current.get("playerHash") != player_hash
            or current.get("playerUrl") != player_url
        ):
            return True
    return False


def _merge_aliases(
    existing: tuple[str, ...],
    player_hash: str,
    md5_alias: str,
    primary_hash: str,
) -> tuple[str, ...]:
    values: list[str] = []
    for value in (*existing, player_hash, md5_alias):
        if value != primary_hash and value not in values:
            values.append(value)
    return tuple(values)


def _dedupe_pairs(pairs: list[CandidatePair]) -> list[CandidatePair]:
    seen: set[tuple[str, str]] = set()
    output: list[CandidatePair] = []
    for pair in pairs:
        key = (pair.sig, pair.n_class)
        if key in seen:
            continue
        seen.add(key)
        output.append(pair)
    return output


def _fail(
    stage: str,
    player_hash: str,
    message: str,
    *,
    player_url: str | None = None,
    details: dict[str, object] | None = None,
) -> ProbeResult:
    return ProbeResult(
        ok=False,
        failure=ProbeFailure(
            stage=stage,
            player_hash=player_hash,
            message=message,
            player_url=player_url,
            details=details,
        ),
    )

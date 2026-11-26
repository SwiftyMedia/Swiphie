"""Command-line interface for Swiphie."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError
from .deriver import derive_player_config, derived_to_entry
from .discovery import discover_player_candidates
from .notify import notify_probe_failure
from .parser import format_paste_ready_entry
from .player_source import load_player_source
from .probe import run_player_probe
from .verify import verify_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swiphie",
        description=(
            "Derive and validate YouTube player cipher configs and publish "
            "a SwiftyTube-compatible registry."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser(
        "probe",
        help="discover, derive, validate and publish one player config",
    )
    probe.add_argument("--check", action="store_true", help="report new players only")
    probe.add_argument(
        "--dry-run",
        action="store_true",
        help="validate but do not write registry files",
    )
    probe.add_argument("--hash", dest="player_hash", help="probe a specific player hash")
    probe.add_argument("--url", dest="player_url", help="probe a specific player.js URL")
    probe.add_argument(
        "--player-file",
        type=Path,
        help="probe a local player.js file instead of the network",
    )
    probe.add_argument(
        "--no-update-current",
        action="store_true",
        help="do not move the registry's current pointer",
    )
    probe.add_argument("--json", action="store_true", help="print machine-readable JSON")

    discover = subparsers.add_parser(
        "discover",
        help="list currently discoverable player candidates",
    )
    discover.add_argument("--json", action="store_true", help="print machine-readable JSON")

    derive = subparsers.add_parser(
        "derive",
        help="derive a config without validating or writing it",
    )
    derive.add_argument("--hash", dest="player_hash", help="player hash")
    derive.add_argument("--url", dest="player_url", help="player.js URL")
    derive.add_argument("--player-file", type=Path, help="local player.js file")
    derive.add_argument(
        "--paste-ready",
        action="store_true",
        help="print a paste-ready player_configs.json entry",
    )
    derive.add_argument("--json", action="store_true", help="print machine-readable JSON")

    verify = subparsers.add_parser(
        "verify",
        help="verify registry/config artifacts (used by CI)",
    )
    verify.add_argument("--json", action="store_true", help="print machine-readable JSON")

    config = subparsers.add_parser("config", help="print resolved configuration")
    config.add_argument("--json", action="store_true", help="print machine-readable JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "probe":
            return _cmd_probe(config, args)
        if args.command == "discover":
            return _cmd_discover(config, args)
        if args.command == "derive":
            return _cmd_derive(config, args)
        if args.command == "verify":
            return _cmd_verify(config, args)
        if args.command == "config":
            return _cmd_config(config, args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - keep the CLI readable
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command {args.command}")
    return 2


def _cmd_probe(config: Config, args: argparse.Namespace) -> int:
    result = run_player_probe(
        config,
        check_only=args.check,
        player_hash=args.player_hash,
        player_url=args.player_url,
        player_file=args.player_file,
        update_current=not args.no_update_current,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    elif result.ok and result.success:
        success = result.success
        print(f"{success.action}: {success.player_hash}")
        if success.player_url:
            print(f"  player: {success.player_url}")
        if success.stream_mode:
            print(f"  stream: {success.stream_mode}")
        if success.new_player_hashes:
            print(f"  new:    {', '.join(success.new_player_hashes)}")
        if success.details:
            winner = success.details.get("winner")
            if winner:
                print(f"  entry:  {json.dumps(winner, separators=(',', ':'))}")
    else:
        failure = result.failure
        if failure is not None:
            print(
                f"probe failed [{failure.stage}] {failure.player_hash}: "
                f"{failure.message}",
                file=sys.stderr,
            )
            if failure.details:
                print(
                    json.dumps(failure.details, indent=2, ensure_ascii=False),
                    file=sys.stderr,
                )
            notify_probe_failure(config, failure)
        else:
            print("probe failed without details", file=sys.stderr)
    return 0 if result.ok else 1


def _cmd_discover(config: Config, args: argparse.Namespace) -> int:
    candidates = discover_player_candidates(config)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "playerHash": candidate.player_hash,
                        "playerUrl": candidate.player_url,
                        "source": candidate.source,
                    }
                    for candidate in candidates
                ],
                indent=2,
            )
        )
    else:
        for candidate in candidates:
            print(f"{candidate.player_hash}  {candidate.source:12s}  {candidate.player_url}")
    return 0


def _cmd_derive(config: Config, args: argparse.Namespace) -> int:
    source = load_player_source(
        config,
        player_file=args.player_file,
        player_url=args.player_url,
        player_hash=args.player_hash,
    )
    derived = derive_player_config(source.player_code, known_hash=source.player_hash)
    entry = derived_to_entry(derived, source.player_hash)
    payload = {
        "playerHash": source.player_hash,
        "playerUrl": source.player_url,
        "sha256": source.sha256,
        "derived": derived.to_dict(),
        "entry": entry.to_dict() if entry else None,
        "pasteReady": (
            format_paste_ready_entry(source.player_hash, entry) if entry else None
        ),
    }
    if args.paste_ready:
        if entry is None:
            print("could not derive a complete entry", file=sys.stderr)
            return 1
        print(format_paste_ready_entry(source.player_hash, entry))
        return 0
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"player: {source.player_hash}")
        if source.player_url:
            print(f"url:    {source.player_url}")
        print(f"sha256: {source.sha256}")
        print(f"sts:    {derived.sts}")
        print(f"sig:    {derived.sig}")
        print(f"nClass: {derived.n_class}")
        print(f"alias:  {derived.md5_alias}")
        if entry:
            print(f"entry:  {format_paste_ready_entry(source.player_hash, entry)}")
    return 0 if entry else 1


def _cmd_verify(config: Config, args: argparse.Namespace) -> int:
    errors = verify_artifacts(config)
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
    elif errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
    else:
        print(
            f"ok: {config.player_configs_path} and "
            f"{config.player_registry_path} are consistent"
        )
    return 0 if not errors else 1


def _cmd_config(config: Config, args: argparse.Namespace) -> int:
    payload = config.as_public_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

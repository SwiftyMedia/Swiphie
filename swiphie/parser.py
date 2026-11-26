"""Schema v1 parser and writer.

This parser is deliberately strict and acts as the trust boundary for remote
data: every value is regex-locked so a
compromised registry can never inject free-form JavaScript into a consumer's
cipher WebView.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SCHEMA_VERSION = 1

SIG_RE = re.compile(r"^[A-Za-z0-9$_]{1,8}\(\d+,\d+,INPUT\)$")
NCLASS_RE = re.compile(r"^[A-Za-z0-9$_]{1,8}$")
PLAYER_HASH_RE = re.compile(r"^[a-f0-9]{8}$")

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_INT_TOKEN_RE = re.compile(r"-?(?:0|[1-9]\d*)\Z")


@dataclass(frozen=True)
class PlayerConfigEntry:
    """One cipher configuration entry."""

    sig: str
    n_class: str
    sts: int
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "sig": self.sig,
            "nClass": self.n_class,
            "sts": self.sts,
        }
        if self.aliases:
            data["aliases"] = list(self.aliases)
        return data


@dataclass
class PlayerConfigsFile:
    """The raw ``player_configs.json`` document."""

    schema_version: int = SUPPORTED_SCHEMA_VERSION
    players: dict[str, PlayerConfigEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "players": {
                player_hash: entry.to_dict()
                for player_hash, entry in self.players.items()
            },
        }

    def to_json(self) -> str:
        # Keep one compact entry per line for readable rotation diffs
        lines: list[str] = []
        for player_hash, entry in self.players.items():
            fields = [
                f'"sig": "{entry.sig}"',
                f'"nClass": "{entry.n_class}"',
                f'"sts": {entry.sts}',
            ]
            if entry.aliases:
                aliases = json.dumps(
                    list(entry.aliases),
                    separators=(", ", ": "),
                    ensure_ascii=False,
                )
                fields.append(f'"aliases": {aliases}')
            lines.append(
                f'    "{player_hash}": {{ ' + ", ".join(fields) + " }"
            )
        body = ",\n".join(lines)
        players = "{\n" + body + "\n  }" if body else "{}"
        return (
            "{\n"
            f'  "schemaVersion": {self.schema_version},\n'
            f'  "players": {players}\n'
            "}\n"
        )


@dataclass
class ParseResult:
    ok: bool
    configs: dict[str, PlayerConfigEntry] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    reason: str | None = None

    def failure(self) -> str:
        return self.reason or "unknown parse failure"


def build_n_js_expression(n_class: str) -> str:
    """Build the n-transform IIFE used by the cipher runtime."""

    return (
        "(function(n){try{var u=new g."
        + n_class
        + "('https://x.googlevideo.com/videoplayback?n='+n,true);"
        + "var t=u.get('n');return(t&&t!==n)?t:n;}catch(e){return n;}})(INPUT)"
    )


def empty_player_configs() -> PlayerConfigsFile:
    return PlayerConfigsFile(schema_version=SUPPORTED_SCHEMA_VERSION, players={})


def parse_player_configs(json_text: str) -> ParseResult:
    """Parse and validate a schema-v1 document.

    Invalid entries are skipped (and reported), but hash/alias collisions
    reject the whole document.
    """

    invalid_number = find_noncanonical_json_number(json_text)
    if invalid_number is not None:
        return ParseResult(
            ok=False,
            reason=f"non-canonical integer {invalid_number!r}",
        )

    try:
        root = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return ParseResult(ok=False, reason=f"malformed JSON: {exc}")

    if not isinstance(root, dict):
        return ParseResult(ok=False, reason="root is not a JSON object")

    schema_version = read_int_primitive(root.get("schemaVersion"))
    if schema_version is None:
        return ParseResult(
            ok=False,
            reason="schemaVersion missing or not an int",
        )
    if schema_version <= 0:
        return ParseResult(ok=False, reason="schemaVersion must be positive")
    if schema_version > SUPPORTED_SCHEMA_VERSION:
        return ParseResult(
            ok=False,
            reason=(
                f"unsupported schemaVersion {schema_version} "
                f"(supported: {SUPPORTED_SCHEMA_VERSION})"
            ),
        )

    players = root.get("players")
    if not isinstance(players, dict):
        return ParseResult(ok=False, reason="players missing or not an object")

    configs: dict[str, PlayerConfigEntry] = {}
    skipped: list[str] = []

    for player_hash, entry_value in players.items():
        parsed = parse_entry(player_hash, entry_value)
        if parsed is None:
            skipped.append(player_hash)
            continue
        entry, aliases = parsed
        keys = [player_hash, *aliases]
        duplicate = _first_duplicate(keys, configs)
        if duplicate is not None:
            return ParseResult(
                ok=False,
                reason=f"duplicate hash/alias {duplicate!r} (entry {player_hash})",
            )
        configs[player_hash] = entry
        for alias in aliases:
            configs[alias] = entry

    return ParseResult(ok=True, configs=configs, skipped=skipped)


def validate_player_configs_text(
    json_text: str,
    label: str = "player configs",
) -> PlayerConfigsFile:
    """Parse a document and reject skipped entries or collisions."""

    result = parse_player_configs(json_text)
    if not result.ok:
        raise ValueError(f"invalid {label}: {result.failure()}")
    if result.skipped:
        raise ValueError(
            f"invalid {label}: skipped entries {', '.join(result.skipped)}"
        )

    root = json.loads(json_text)
    players: dict[str, PlayerConfigEntry] = {}
    for player_hash, entry_value in root["players"].items():
        parsed = parse_entry(player_hash, entry_value)
        if parsed is None:  # pragma: no cover - parse already succeeded
            raise ValueError(f"invalid {label}: entry {player_hash} failed re-parse")
        players[player_hash] = parsed[0]
    return PlayerConfigsFile(
        schema_version=int(root["schemaVersion"]),
        players=players,
    )


def read_player_configs_file(path: Path) -> PlayerConfigsFile:
    return validate_player_configs_text(path.read_text(encoding="utf-8"), str(path))


def load_or_empty_player_configs(path: Path) -> PlayerConfigsFile:
    if not path.exists():
        return empty_player_configs()
    return read_player_configs_file(path)


def merge_player_config_entry(
    path: Path,
    player_hash: str,
    entry: PlayerConfigEntry,
) -> PlayerConfigsFile:
    """Insert/replace one entry, sort by sts, validate and write atomically."""

    if not PLAYER_HASH_RE.fullmatch(player_hash):
        raise ValueError(f"invalid player hash {player_hash!r}")

    file = load_or_empty_player_configs(path)
    file.players[player_hash] = entry
    file.players = sort_players_by_sts(file.players)

    text = file.to_json()
    validated = validate_player_configs_text(text, str(path))
    if player_hash not in validated.players:
        raise ValueError(f"generated {path} is missing primary hash {player_hash}")
    write_text_atomic(path, text)
    return validated


def sort_players_by_sts(
    players: dict[str, PlayerConfigEntry],
) -> dict[str, PlayerConfigEntry]:
    return dict(
        sorted(
            players.items(),
            key=lambda item: (item[1].sts, item[0]),
        )
    )


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def find_committed_entry(
    players: dict[str, PlayerConfigEntry],
    player_hash: str,
    md5_alias: str,
) -> tuple[str, PlayerConfigEntry] | None:
    for primary_hash, entry in players.items():
        if (
            primary_hash == player_hash
            or primary_hash == md5_alias
            or player_hash in entry.aliases
            or md5_alias in entry.aliases
        ):
            return primary_hash, entry
    return None


def format_paste_ready_entry(
    player_hash: str,
    entry: PlayerConfigEntry,
) -> str:
    aliases = json.dumps(list(entry.aliases), separators=(",", ":"))
    return (
        f'"{player_hash}": {{ "sig": "{entry.sig}", '
        f'"nClass": "{entry.n_class}", "sts": {entry.sts}, '
        f'"aliases": {aliases} }}'
    )


def parse_entry(
    player_hash: object,
    value: object,
) -> tuple[PlayerConfigEntry, list[str]] | None:
    if not isinstance(player_hash, str) or not PLAYER_HASH_RE.fullmatch(player_hash):
        return None
    if not isinstance(value, dict):
        return None

    sig = value.get("sig")
    if not isinstance(sig, str) or not SIG_RE.fullmatch(sig):
        return None

    n_class = value.get("nClass")
    if not isinstance(n_class, str) or not NCLASS_RE.fullmatch(n_class):
        return None

    sts = read_int_primitive(value.get("sts"))
    if sts is None or sts <= 0:
        return None

    aliases = parse_aliases(value.get("aliases"))
    if aliases is None:
        return None

    return PlayerConfigEntry(sig=sig, n_class=n_class, sts=sts, aliases=tuple(aliases)), aliases


def parse_aliases(value: object) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    aliases: list[str] = []
    for element in value:
        if not isinstance(element, str) or not PLAYER_HASH_RE.fullmatch(element):
            return None
        aliases.append(element)
    return aliases


def read_int_primitive(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < _INT32_MIN or value > _INT32_MAX:
        return None
    return value


def _first_duplicate(keys: list[str], configs: dict[str, PlayerConfigEntry]) -> str | None:
    seen: set[str] = set()
    for key in keys:
        if key in seen or key in configs:
            return key
        seen.add(key)
    return None


def find_noncanonical_json_number(json_text: str) -> str | None:
    """Reject floats, exponents and out-of-range integers outside strings.

    This keeps the accepted document shape consistent for downstream readers.
    """

    in_string = False
    escaped = False
    index = 0
    length = len(json_text)
    while index < length:
        char = json_text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char != "-" and not ("0" <= char <= "9"):
            index += 1
            continue

        end = index + 1
        while end < length and json_text[end] in "0123456789.eE+-":
            end += 1
        token = json_text[index:end]
        if _INT_TOKEN_RE.fullmatch(token):
            try:
                value = int(token)
            except ValueError:
                value = None
            if value is not None and _INT32_MIN <= value <= _INT32_MAX:
                index = end
                continue
        return token
    return None

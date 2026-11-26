"""Derive cipher config fields from a YouTube ``player_ias`` base.js.

The extractor uses the ``.set("alr","yes")`` assembly landmark, then falls
back to enumerating plausible ``sig``/``nClass`` pairs for validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .hash import content_alias
from .parser import NCLASS_RE, SIG_RE, PlayerConfigEntry

STS_PATTERN = re.compile(r"""signatureTimestamp['":\s]+(\d{4,6})""")
LOOSE_STS_PATTERN = re.compile(r"""sts['":\s]+(\d+)""")
URL_HASH_PATTERN = re.compile(r"/s/player/([a-f0-9]{8})/")

N_CLASS_ALR_PATTERN = re.compile(
    r'new\s+g\.([A-Za-z0-9$_]{1,8})\s*\([^,()]+,\s*!0\s*\)\s*;\s*'
    r'[A-Za-z0-9$_]+\.set\("alr","yes"\)'
)
SIG_ALR_PATTERN = re.compile(
    r'([A-Za-z0-9$_]{1,8})\((\d+),(\d+),\s*'
    r'([A-Za-z0-9$_]{1,8})\((\d+),(\d+),'
)
N_GET_PATTERN = re.compile(
    r'new\s+g\.([A-Za-z0-9$_]{1,8})\([^)]*\)\)?\s*\.\s*get\("n"\)'
)
ALR_N_PATTERN = re.compile(
    r'new\s+g\.([A-Za-z0-9$_]{1,8})\([^,()]+,\s*!0\s*\)\s*;\s*'
    r'[A-Za-z0-9$_]+\.set\("alr"'
)
ALR_SIG_PATTERN = re.compile(
    r'=\s*([A-Za-z0-9$_]{1,8})\((\d+),(\d+),\s*'
    r'[A-Za-z0-9$_]{1,8}\(\d+,\d+,'
)

ALR_MARKER = '.set("alr","yes")'


@dataclass(frozen=True)
class DerivedPlayerConfig:
    url_hash: str | None
    md5_alias: str
    sts: int | None
    sig: str | None
    n_class: str | None
    n_class_confirmed: bool
    decode_wrapper: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "urlHash": self.url_hash,
            "md5Alias": self.md5_alias,
            "sts": self.sts,
            "sig": self.sig,
            "nClass": self.n_class,
            "nClassConfirmed": self.n_class_confirmed,
            "decodeWrapper": self.decode_wrapper,
        }


@dataclass(frozen=True)
class CandidatePair:
    sig: str
    n_class: str


@dataclass
class CandidateEnumeration:
    sig_exprs: list[str] = field(default_factory=list)
    n_classes: list[str] = field(default_factory=list)
    pairs: list[CandidatePair] = field(default_factory=list)


def derive_player_config(
    player_js: str,
    known_hash: str | None = None,
) -> DerivedPlayerConfig:
    """Extract ``sts``, ``sig``, ``nClass`` and the content alias."""

    sts_match = STS_PATTERN.search(player_js)
    sts = int(sts_match.group(1)) if sts_match else None
    if sts is None:
        loose = LOOSE_STS_PATTERN.search(player_js)
        sts = int(loose.group(1)) if loose else None

    url_hash = known_hash
    if url_hash is None:
        url_match = URL_HASH_PATTERN.search(player_js)
        url_hash = url_match.group(1) if url_match else None

    md5_alias = content_alias(player_js)

    alr = player_js.find(ALR_MARKER)
    n_class: str | None = None
    sig: str | None = None
    decode_wrapper: str | None = None

    if alr >= 0:
        window = player_js[max(0, alr - 80) : alr + 160]
        n_match = N_CLASS_ALR_PATTERN.search(window)
        if n_match:
            n_class = n_match.group(1)

        alr_slice = window[window.find('"alr"') :]
        sig_match = SIG_ALR_PATTERN.search(alr_slice)
        if sig_match:
            sig = f"{sig_match.group(1)}({sig_match.group(2)},{sig_match.group(3)},INPUT)"
            decode_wrapper = (
                f"{sig_match.group(4)}({sig_match.group(5)},{sig_match.group(6)},…)"
            )

    n_class_confirmed = bool(
        n_class and _n_class_confirmed(player_js, n_class)
    )

    # Use broad candidates only when the assembly landmark is incomplete
    if sig is None or n_class is None:
        candidates = enumerate_candidate_pairs(player_js)
        if sig is None and candidates.sig_exprs:
            sig = candidates.sig_exprs[0]
        if n_class is None and candidates.n_classes:
            n_class = candidates.n_classes[0]
        if n_class and not n_class_confirmed:
            n_class_confirmed = _n_class_confirmed(player_js, n_class)

    return DerivedPlayerConfig(
        url_hash=url_hash,
        md5_alias=md5_alias,
        sts=sts,
        sig=sig,
        n_class=n_class,
        n_class_confirmed=n_class_confirmed,
        decode_wrapper=decode_wrapper,
    )


def enumerate_candidate_pairs(player_js: str) -> CandidateEnumeration:
    """Enumerate plausible ``sig``/``nClass`` pairs for stream validation."""

    sig_exprs: list[str] = []
    n_classes: list[str] = []

    for match in N_GET_PATTERN.finditer(player_js):
        value = match.group(1)
        if value not in n_classes:
            n_classes.append(value)

    alr = player_js.find(ALR_MARKER)
    if alr >= 0:
        window = player_js[max(0, alr - 120) : alr + 10]
        n_match = ALR_N_PATTERN.search(window)
        if n_match:
            value = n_match.group(1)
            if value not in n_classes:
                n_classes.insert(0, value)

    for match in re.finditer(re.escape(ALR_MARKER), player_js):
        index = match.start()
        window = player_js[index : index + 220]
        sig_match = ALR_SIG_PATTERN.search(window)
        if sig_match:
            expr = f"{sig_match.group(1)}({sig_match.group(2)},{sig_match.group(3)},INPUT)"
            if expr not in sig_exprs:
                sig_exprs.append(expr)

    pairs = [
        CandidatePair(sig=sig, n_class=n_class)
        for sig in sig_exprs
        for n_class in n_classes
    ]
    return CandidateEnumeration(
        sig_exprs=sig_exprs,
        n_classes=n_classes,
        pairs=pairs,
    )


def derived_to_entry(
    derived: DerivedPlayerConfig,
    player_hash: str,
) -> PlayerConfigEntry | None:
    """Convert a derived config into a validated registry entry."""

    if not derived.sig or not derived.n_class or not derived.sts:
        return None
    if not SIG_RE.fullmatch(derived.sig) or not NCLASS_RE.fullmatch(derived.n_class):
        return None
    aliases: tuple[str, ...] = ()
    if derived.md5_alias != player_hash:
        aliases = (derived.md5_alias,)
    return PlayerConfigEntry(
        sig=derived.sig,
        n_class=derived.n_class,
        sts=derived.sts,
        aliases=aliases,
    )


def extract_signature_timestamp(player_js: str) -> int | None:
    match = STS_PATTERN.search(player_js)
    if match:
        return int(match.group(1))
    loose = LOOSE_STS_PATTERN.search(player_js)
    return int(loose.group(1)) if loose else None


def _n_class_confirmed(player_js: str, n_class: str) -> bool:
    escaped = re.escape(n_class)
    direct = re.search(
        rf'new\s+g\.{escaped}\([^)]*\)\)?\s*\.\s*get\("n"\)',
        player_js,
    )
    if direct:
        return True
    return bool(
        re.search(rf"g\.{escaped}\b", player_js) and '.get("n")' in player_js
    )

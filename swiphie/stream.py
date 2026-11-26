"""Live stream validation.

A derived config is only accepted when its deobfuscated URL makes the CDN
return a strict HTTP 206 audio range response.  That is the ground truth:
multiple constant pairs can "decipher" a signature, but only the real one is
accepted by the CDN.
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Sequence
from urllib.parse import parse_qs

from .cipher import CipherError, evaluate_cipher_candidate
from .config import Config
from .credentials import InnertubeCred, sapisid_hash
from .deriver import CandidatePair
from .http import HttpError, fetch_text, post_json
from .parser import PlayerConfigEntry

ORIGIN = "https://music.youtube.com"
WEB_REMIX = {
    "clientName": "WEB_REMIX",
    "clientId": "67",
}
DEFAULT_CLIENT_VERSION = "1.20260213.01.00"
DEFAULT_FETCH_TIMEOUT = 10.0
RETRY_FETCH_TIMEOUT = 15.0
RETRY_DELAY = 1.0
DEFAULT_RUNTIME_TIMEOUT = 15.0
DEFAULT_VALIDATION_DEADLINE = 120.0
MAX_CANDIDATE_PAIRS = 24
MIN_STREAM_BYTES = 1024


@dataclass(frozen=True)
class StreamSettings:
    timeout: float = DEFAULT_FETCH_TIMEOUT
    retry_timeout: float = RETRY_FETCH_TIMEOUT
    runtime_timeout: float = DEFAULT_RUNTIME_TIMEOUT
    deadline: float = DEFAULT_VALIDATION_DEADLINE
    max_pairs: int = MAX_CANDIDATE_PAIRS
    client_version: str = DEFAULT_CLIENT_VERSION
    web_ua: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) "
        "Gecko/20100101 Firefox/140.0"
    )

    @classmethod
    def from_config(cls, config: Config) -> "StreamSettings":
        return cls(
            timeout=config.fetch_timeout,
            retry_timeout=config.retry_timeout,
            runtime_timeout=config.runtime_timeout,
            deadline=config.validation_deadline,
            max_pairs=config.max_candidate_pairs,
            client_version=config.client_version or DEFAULT_CLIENT_VERSION,
            web_ua=config.web_ua,
        )


def resolve_client_version(config: Config) -> str:
    """Return the pinned client version or discover the current WEB_REMIX one.

    A stale pinned version is a common cause of validation failures.  When the
    operator has not set ``SWIPHIE_CLIENT_VERSION``, read the value YouTube
    itself serves on the music home page and fall back to the pinned default.
    """

    if config.client_version:
        return config.client_version
    try:
        html = fetch_text(
            "https://music.youtube.com/",
            headers={
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=config.fetch_timeout,
            max_bytes=config.max_watch_bytes,
        )
        match = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"]+)"', html)
        if match:
            return match.group(1)
    except HttpError:
        pass
    return DEFAULT_CLIENT_VERSION


@dataclass(frozen=True)
class CipherSamples:
    ciphers: list[str]
    mode: str


@dataclass
class ValidationAttempt:
    sig: str
    n_class: str
    n_probe_changed: bool | None
    n_probe_valid: bool | None
    injection_mode: str
    status: str
    sample_statuses: list[str]
    works: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "sig": self.sig,
            "nClass": self.n_class,
            "nProbeChanged": self.n_probe_changed,
            "nProbeValid": self.n_probe_valid,
            "injectionMode": self.injection_mode,
            "status": self.status,
            "sampleStatuses": self.sample_statuses,
            "works": self.works,
        }


@dataclass
class ValidationWinner:
    player_hash: str
    md5_alias: str
    sts: int
    sig: str
    n_class: str
    entry: PlayerConfigEntry


@dataclass
class ValidationResult:
    player_hash: str
    md5_alias: str
    sts: int
    attempts: list[ValidationAttempt] = field(default_factory=list)
    winner: ValidationWinner | None = None
    ambiguous: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "playerHash": self.player_hash,
            "md5Alias": self.md5_alias,
            "sts": self.sts,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "winner": (
                {
                    "playerHash": self.winner.player_hash,
                    "md5Alias": self.winner.md5_alias,
                    "sts": self.winner.sts,
                    "sig": self.winner.sig,
                    "nClass": self.winner.n_class,
                    "entry": self.winner.entry.to_dict(),
                }
                if self.winner
                else None
            ),
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True)
class CdnProbeResult:
    status: str
    valid: bool
    bytes_read: int
    content_type: str | None
    content_range: str | None


def build_player_request(
    sts: int,
    video_id: str,
    cred: InnertubeCred | None,
    client_version: str,
) -> dict[str, object]:
    return {
        "context": {
            "client": {
                "clientName": WEB_REMIX["clientName"],
                "clientVersion": client_version,
                "gl": "US",
                "hl": "en",
                **(
                    {"visitorData": cred.visitor_data}
                    if cred and cred.visitor_data
                    else {}
                ),
            },
        },
        "videoId": video_id,
        "playbackContext": {
            "contentPlaybackContext": {"signatureTimestamp": sts},
        },
    }


def build_player_headers(
    cred: InnertubeCred | None,
    settings: StreamSettings,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": settings.web_ua,
        "X-Goog-Api-Format-Version": "1",
        "X-YouTube-Client-Name": WEB_REMIX["clientId"],
        "X-YouTube-Client-Version": settings.client_version,
        "X-Origin": ORIGIN,
        "Referer": f"{ORIGIN}/",
    }
    if cred and cred.visitor_data:
        headers["X-Goog-Visitor-Id"] = cred.visitor_data
    if cred and cred.cookie:
        headers["cookie"] = cred.cookie
        authorization = sapisid_hash(cred.cookie)
        if authorization:
            headers["Authorization"] = authorization
    return headers


def request_signature_cipher(
    sts: int,
    video_id: str,
    cred: InnertubeCred | None,
    settings: StreamSettings,
    timeout: float,
) -> str:
    response = post_json(
        f"{ORIGIN}/youtubei/v1/player?prettyPrint=false",
        build_player_request(sts, video_id, cred, settings.client_version),
        headers=build_player_headers(cred, settings),
        timeout=timeout,
        max_bytes=2_000_000,
    )
    if response.status != 200:
        raise RuntimeError(f"player request failed: HTTP {response.status}")
    text = response.text()
    if not text:
        raise RuntimeError("player fetch returned no body")

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"player response was not JSON: {exc}") from exc

    playability = payload.get("playabilityStatus") or {}
    status = playability.get("status")
    if status and status != "OK":
        reason = playability.get("reason") or ""
        raise RuntimeError(f"player response not OK: {status} {reason}".strip())

    formats = (payload.get("streamingData") or {}).get("adaptiveFormats") or []
    for item in formats:
        if not isinstance(item, dict):
            continue
        cipher = item.get("signatureCipher")
        mime = item.get("mimeType") or ""
        if isinstance(cipher, str) and mime.startswith("audio/"):
            return cipher
    raise RuntimeError(
        "could not get signatureCipher from player response (url/sabr?)"
    )


def fetch_signature_cipher(
    sts: int,
    video_id: str,
    *,
    cred: InnertubeCred | None = None,
    settings: StreamSettings | None = None,
    prefer_auth: bool = False,
) -> tuple[str, str]:
    """Return ``(cipher, mode)`` using guest first unless auth is preferred."""

    settings = settings or StreamSettings()
    order = (
        ["authenticated", "guest"]
        if prefer_auth and cred and cred.cookie
        else ["guest", "authenticated"]
    )
    errors: list[str] = []
    for mode in order:
        if mode == "authenticated" and (not cred or not cred.cookie):
            continue
        for attempt in range(2):
            if attempt > 0:
                time.sleep(RETRY_DELAY)
            request_timeout = (
                settings.timeout
                if attempt == 0
                else max(settings.timeout, settings.retry_timeout)
            )
            try:
                cipher = request_signature_cipher(
                    sts,
                    video_id,
                    cred if mode == "authenticated" else None,
                    settings,
                    request_timeout,
                )
                return cipher, mode
            except (HttpError, RuntimeError) as exc:
                if attempt == 1:
                    errors.append(f"{mode}: {exc}")

    if not cred or not cred.cookie:
        errors.append("authenticated: unavailable (YT_COOKIE is not configured)")
    raise RuntimeError(
        f"could not fetch signatureCipher for {video_id}"
        + (f" ({'; '.join(errors)})" if errors else "")
    )


def fetch_validation_ciphers(
    sts: int,
    video_ids: Sequence[str],
    *,
    cred: InnertubeCred | None = None,
    settings: StreamSettings | None = None,
    prefer_auth: bool = False,
) -> CipherSamples:
    settings = settings or StreamSettings()
    videos = list(dict.fromkeys(video_ids))
    if len(videos) < 2:
        raise RuntimeError("validation requires at least two distinct video IDs")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) for video_id in videos):
        raise RuntimeError("validation video IDs are invalid")

    ciphers: list[str] = []
    signatures: set[str] = set()
    modes: list[str] = []
    errors: list[str] = []
    for video_id in videos:
        try:
            cipher, mode = fetch_signature_cipher(
                sts,
                video_id,
                cred=cred,
                settings=settings,
                prefer_auth=prefer_auth,
            )
            signature = _signature_from_cipher(cipher)
            if not signature or signature in signatures:
                raise RuntimeError(
                    "player response did not provide a distinct signature"
                )
            ciphers.append(cipher)
            signatures.add(signature)
            modes.append(mode)
            if len(ciphers) == 2:
                return CipherSamples(
                    ciphers=ciphers,
                    mode="authenticated" if "authenticated" in modes else "guest",
                )
        except RuntimeError as exc:
            message = f"{video_id}: {exc}"
            errors.append(message)
            print(f"Skipping validation video {message}")

    raise RuntimeError(
        f"could not fetch two distinct validation ciphers "
        f"(got {len(ciphers)}; {'; '.join(errors)})"
    )


def probe_cdn_stream(
    url: str,
    settings: StreamSettings | None = None,
    *,
    range_header: str = "bytes=0-262143",
    timeout: float | None = None,
) -> CdnProbeResult:
    settings = settings or StreamSettings()
    if not is_google_video_playback_url(url):
        return _cdn_failure("ERR:invalid CDN URL")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": settings.web_ua,
            "Range": range_header,
            "Connection": "close",
        },
    )
    response = None
    try:
        try:
            response = urllib.request.urlopen(
                request,
                timeout=timeout if timeout is not None else settings.timeout,
            )
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            return _cdn_failure(f"ERR:{str(exc)[:30]}")

        status = int(getattr(response, "status", 0) or 0)
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type")
        content_range = response.headers.get("Content-Range")

        if (
            status != 206
            or not content_type
            or not content_type.lower().startswith("audio/")
            or not content_range
            or not re.fullmatch(r"bytes 0-\d+/(?:\d+|\*)", content_range, re.I)
            or not is_google_video_playback_url(final_url)
        ):
            return CdnProbeResult(
                status=str(status),
                valid=False,
                bytes_read=0,
                content_type=content_type,
                content_range=content_range,
            )

        bytes_read = 0
        while bytes_read < MIN_STREAM_BYTES:
            chunk = response.read(MIN_STREAM_BYTES - bytes_read)
            if not chunk:
                break
            bytes_read += len(chunk)
        return CdnProbeResult(
            status=str(status),
            valid=bytes_read >= MIN_STREAM_BYTES,
            bytes_read=bytes_read,
            content_type=content_type,
            content_range=content_range,
        )
    except Exception as exc:  # noqa: BLE001 - a probe failure is data
        return _cdn_failure(f"ERR:{str(exc)[:30]}")
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001 - best effort close
                pass


def validate_candidate_pairs(
    config: Config,
    *,
    player_js: str,
    player_hash: str,
    md5_alias: str,
    sts: int,
    signature_ciphers: Sequence[str],
    pairs: Sequence[CandidatePair],
    settings: StreamSettings | None = None,
) -> ValidationResult:
    settings = settings or StreamSettings.from_config(config)
    if len(pairs) > settings.max_pairs:
        raise RuntimeError(
            f"refusing to validate {len(pairs)} candidate pairs "
            f"(max {settings.max_pairs})"
        )
    ciphers = list(signature_ciphers)
    if not ciphers:
        raise RuntimeError("at least one signatureCipher is required")

    signatures = [_signature_from_cipher(cipher) for cipher in ciphers]
    if any(not signature for signature in signatures):
        raise RuntimeError("signatureCipher missing s")
    if len(set(signatures)) != len(signatures):
        raise RuntimeError("validation signatureCipher samples must be distinct")

    attempts: list[ValidationAttempt] = []
    passing: list[CandidatePair] = []
    deadline = time.monotonic() + settings.deadline

    for pair in pairs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("candidate validation deadline exceeded")

        status = "n/a"
        sample_statuses: list[str] = []
        n_probe_changed: bool | None = None
        n_probe_valid: bool | None = None
        injection_mode = "global-fallback"
        works = False
        try:
            evaluation = evaluate_cipher_candidate(
                config,
                player_js=player_js,
                sig_expr=pair.sig,
                n_class=pair.n_class,
                signature_ciphers=ciphers,
                timeout=min(settings.runtime_timeout, remaining),
            )
            injection_mode = evaluation.injection_mode
            n_probe_changed = evaluation.n_probe.changed
            n_probe_valid = evaluation.n_probe.valid
            if evaluation.error or not evaluation.urls:
                raise RuntimeError(evaluation.error or "cipher returned no URLs")

            probes: list[CdnProbeResult] = []
            for url in evaluation.urls:
                probe = _probe_with_retry(
                    url,
                    settings,
                    deadline,
                )
                probes.append(probe)
            sample_statuses = [probe.status for probe in probes]
            status = (
                sample_statuses[0]
                if len(sample_statuses) == 1
                else ",".join(sample_statuses)
            )
            works = (
                evaluation.n_probe.valid is True
                and len(probes) == len(ciphers)
                and all(probe.valid for probe in probes)
            )
        except (CipherError, RuntimeError) as exc:
            status = f"deob-fail:{str(exc)[:60]}"
            sample_statuses = [status]

        attempts.append(
            ValidationAttempt(
                sig=pair.sig,
                n_class=pair.n_class,
                n_probe_changed=n_probe_changed,
                n_probe_valid=n_probe_valid,
                injection_mode=injection_mode,
                status=status,
                sample_statuses=sample_statuses,
                works=works,
            )
        )
        if works:
            passing.append(pair)

    pair = passing[0] if len(passing) == 1 else None
    aliases: tuple[str, ...] = ()
    if md5_alias != player_hash:
        aliases = (md5_alias,)
    winner = (
        ValidationWinner(
            player_hash=player_hash,
            md5_alias=md5_alias,
            sts=sts,
            sig=pair.sig,
            n_class=pair.n_class,
            entry=PlayerConfigEntry(
                sig=pair.sig,
                n_class=pair.n_class,
                sts=sts,
                aliases=aliases,
            ),
        )
        if pair
        else None
    )
    return ValidationResult(
        player_hash=player_hash,
        md5_alias=md5_alias,
        sts=sts,
        attempts=attempts,
        winner=winner,
        ambiguous=len(passing) > 1,
    )


def is_google_video_playback_url(value: str) -> bool:
    try:
        from urllib.parse import urlsplit

        url = urlsplit(value)
    except ValueError:
        return False
    return (
        url.scheme == "https"
        and (url.hostname or "").endswith(".googlevideo.com")
        and url.path == "/videoplayback"
    )


def _probe_with_retry(
    url: str,
    settings: StreamSettings,
    deadline: float,
) -> CdnProbeResult:
    attempt = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("candidate validation deadline exceeded")
        probe = probe_cdn_stream(
            url,
            settings,
            timeout=min(
                settings.timeout if attempt == 0 else settings.retry_timeout,
                remaining,
            ),
        )
        if (
            attempt > 0
            or probe.valid
            or not probe.status.startswith("ERR:")
        ):
            return probe
        attempt += 1


def _signature_from_cipher(cipher: str) -> str | None:
    values = parse_qs(cipher, keep_blank_values=True).get("s")
    return values[0] if values else None


def _cdn_failure(status: str) -> CdnProbeResult:
    return CdnProbeResult(
        status=status,
        valid=False,
        bytes_read=0,
        content_type=None,
        content_range=None,
    )

# Swiphie

Swiphie is a Python registry for YouTube player cipher configuration used by
[SwiftyTube](https://github.com/686udjie/SwiftyTube). It discovers player
rotations, derives signature and `n` transform data, validates candidates
against live audio streams, and writes a SwiftyTube-compatible registry

The design was inspired by [zemer-cipher](https://github.com/ZemerTeam/zemer-cipher)
and [faraday](https://github.com/MetrolistGroup/faraday)

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer
- Network access for discovery and live validation

## Install

```sh
cd Swiphie
python3 -m venv .venv
source .venv/bin/activate
```

Swiphie uses only the Python standard library

## Use

Run offline tests and artifact checks:

```sh
python -m unittest discover -s tests -v
python -m swiphie verify
```

Discover the current player without changing files:

```sh
python -m swiphie discover --json
python -m swiphie probe --check --json
```

Validate a live player without writing registry files:

```sh
python -m swiphie probe --dry-run --json
```

Validate and publish a new registry entry:

```sh
python -m swiphie probe --json
```

Derive from a local player file:

```sh
python -m swiphie derive \
  --player-file tests/fixtures/synthetic_player.js \
  --hash 9244921b \
  --paste-ready
```

## Registry

The public file consumed by SwiftyTube is:

```text
registry/player_configs.json
```

The registry uses the `schemaVersion: 1` format with player hashes, signature
expressions, `nClass` values, signature timestamps, and aliases

`registry/player_registry.json` and `registry/releases/` store provenance and
validation records for CI

SwiftyTube reads the registry from:

```text
https://raw.githubusercontent.com/SwiftyMedia/Swiphie/main/registry/player_configs.json
```

## Validation

A candidate must produce valid URLs for two different videos. Each URL must
return HTTP 206 audio data from `*.googlevideo.com` with at least 1 KiB read

Ambiguous or partially passing candidates are rejected. The last valid
registry remains in place

## Configuration

Settings are controlled by environment variables, including:

- `SWIPHIE_VALIDATION_VIDEO_IDS`
- `SWIPHIE_PLAYER_HASHES`
- `SWIPHIE_CLIENT_VERSION`
- `SWIPHIE_COOKIE_FILE`
- `SWIPHIE_DISCORD_WEBHOOK`

Run this to see all resolved settings:

```sh
python -m swiphie config --json
```

Cookie files and environment secrets are ignored by Git

## CI

`.github/workflows/ci.yml` runs compilation, unit tests, and registry checks

`.github/workflows/probe-player.yml` probes YouTube once every 24 hours and
publishes only when a validated registry change is found

Add a `SWIFTYTUBE_TOKEN` repository secret with Contents write access to the
`SwiftyMedia/SwiftyTube` repository so the workflow can update its bundled
fallback table

## License

GPL-3.0-or-later. See [LICENSE](LICENSE)

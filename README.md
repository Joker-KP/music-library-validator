# Music Library Validator — starter repository

Read-only, rule-based MP3/FLAC quality audit with SQLite metadata caching, folder-based albums, and static HTML/CSV/JSON reports. The CLI includes scan transactions, configuration validation, output-path checks and regression tests. Deployment on Synology and large-library performance still need field testing; limitations are documented below.

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
# Edit config/rules.yaml: library_root: /absolute/path/to/music
music-validator scan --config config/rules.yaml --db data/library.sqlite --reports reports
python -m pytest -q
```

## Docker / Synology

1. Edit `compose.yaml`, replacing `/volume1/music` with your music directory.
2. `mkdir -p data reports`; ensure both folders are writable by the chosen `PUID:PGID` (default 1000:1000).
3. `docker compose build && docker compose run --rm music-validator`
4. Open `reports/index.html` locally; CSV and JSON are alongside it.
5. Subsequent runs use SQLite cache. To rebuild metadata: `docker compose run --rm music-validator scan --config /config/rules.yaml --db /data/library.sqlite --reports /reports --full-scan`.

In Docker use `/music` in `rules.yaml`. Music is mounted `:ro`; SQLite and reports are writable volumes. No web server or port is exposed. Can be scheduled with Synology Task Scheduler or cron. The container starts only for a scan.

The scanner targets Linux, including Ubuntu and Synology, and uses directory
descriptors, `O_NOFOLLOW` and `flock`. Keep database and report directories owned
by the scanning user and outside the music library. Output directories, SQLite
files/sidecars and lock files must not be symlinks; database files must have one
hard link. The report directory must support symbolic links and atomic rename.
Overlapping scans using either the same database or the same report directory
fail immediately with exit code 2.

## Agreed requirements & decisions

- Audit existing MP3 and FLAC files, never rename, rewrite tags, or alter music automatically.
- Required fields include `title`, `artist`, `album`, `tracknumber`, `comment`; embedded art is mandatory by default. Configure rule choices in YAML.
- Support cross-tag relationships (example: `comment == artist + ' - ' + album`) and title/filename conventions; custom equality templates work today.
- Group albums by **directory**, not tags. Directories named `CD1`, `CD 02`, `Disc-3`, `Disk_04`, or ending with a separated disc marker such as `Some Album Name - CD2`, are disc subfolders of their parent. An album with two discs is one report group, and track 1 on disc 1 and disc 2 are not duplicates.
- Report *albums needing attention*, with track-level explanations in expandable HTML sections and machine-readable CSV/JSON.
- SQLite caches parsed metadata by path, size and nanosecond mtime. New/changed files are re-read; removed files are purged after a successful traversal. **All cached entries are revalidated against current YAML rules every run**, so rule edits do not require a metadata rescan.
- Album grouping is recalculated even on metadata cache hits. Cached read errors are retried on the next scan. Complete scans persist summary counters in SQLite.
- Read-only scanner, one-shot Docker execution, no always-on service or opened port, no mandatory external services.
- Default timezone Europe/Warsaw in Compose; editable.

## Examples

Expected naming convention: `01 - Track title.mp3` or `01 - Track title.flac`. `filename.regex` checks the stem; `filename.match_template` can additionally compare it with metadata, for example `{tracknumber:02d} - {title}`. Avoid enabling this template until track number formats and character escaping are confirmed.

`filename.allowed_chars` independently checks characters in the stem, excluding
the extension. Its default is the exact allowlist below: ASCII letters, digits,
spaces, hyphens, square brackets and parentheses. Ranges such as `a-z` expand;
other characters are literal, so brackets do not need escaping. Append any
additional Unicode letters/punctuation used by your collection, or set `''` (or
null) to allow all characters. Other filename checks still apply.

```yaml
filename:
  allowed_chars: '[]0-9a-zA-Z -()'
  # For unrestricted characters instead: allowed_chars: ''
```

For disc folders, the default pattern recognizes a final `CD`, `Disc` or `Disk`
marker with a positive number. A prefix must be separated by a space, underscore,
dot or hyphen: `Album - CD2` and `Album_CD 02` work; `AlbumCD2` does not. Numbers
in the prefix, such as `1999 - Album - CD2`, do not affect disc-number extraction.
An explicit `disc_folder_pattern` overrides this default; update existing YAML
patterns to the example in `config/rules.yaml` to enable suffix recognition.

Example custom rule (replace `custom_rules: []`):

```yaml
custom_rules:
  - id: comment_artist_album
    field: comment
    equals_template: '{artist} - {album}'
    severity: warning
```

Examples of grouping:

```text
/music/Artist/Album/01 - Intro.flac          -> /music/Artist/Album
/music/Artist/Album/CD1/01 - Intro.flac      -> /music/Artist/Album
/music/Artist/Album/CD2/01 - Outro.flac      -> /music/Artist/Album
/music/Artist/Album/Album - CD2/01.flac     -> /music/Artist/Album
/music/Artist/Album/Bonus/01 - Demo.mp3      -> /music/Artist/Album/Bonus (separate)
```

## Implementation status

Implemented: recursive file enumeration, ID3 and FLAC Vorbis Comment normalization via Mutagen, MP3 APIC / FLAC Picture detection, album directory grouping with disc suffixes, disc-folder fallback, missing field / cover / filename regex and allowed-character checks, optional filename template and custom equality rules, album/title and albumartist consistency options, duplicate `(disc, track)` detection, optional cover-hash consistency, SQLite incremental cache, read errors and three report formats.

Not implemented yet: MP3 cover image dimension measurement; FLAC stream integrity check (`flac -t`); hashing music/audio data; resilient path rename detection; semantic comparison of multi-valued tags; nonnumeric disc numbering; a richer declarative rule-expression language; persisted database issue history/deltas; a persistent web UI; reproducible locked dependencies; structured logging and performance benchmarks.

## Verification

Run `python -m pytest -q` after installing `.[dev]`. Tests include real synthetic
MP3 and FLAC fixtures and subprocess CLI scans, Unicode metadata, cache reuse,
YAML-only changes, custom disc patterns, migration, interruption, traversal/read
failures, concurrent ownership, unsafe outputs, and report publication failures.
Fixture audio is copied to temporary directories; FFmpeg is not needed for tests.
Source content hashes, sizes and modification times are checked across scans.

## Safety and edge cases

- Files are **never changed**. SQLite and reports are written outside `/music`.
- Scanner skips symlinked files and directories. Files are opened read-only relative to their directory descriptor without following links, and their stat signatures are checked before/after metadata parsing. Traversal/stat failures abort the scan and preserve the previous cache and reports. Descendant mount points are traversed; actual NAS mount behavior still needs deployment testing.
- Metadata changes and pruning share one SQLite transaction. An interrupted discovery/validation rolls back the entire scan. The cache commits before report publication; a report-write failure can leave an updated cache with the previous complete report set. Rerunning safely revalidates the cache and publishes reports.
- Cache's `(size, mtime_ns)` check can miss external edits that preserve both attributes; `--full-scan` forces metadata reread. No cryptographic change check is implemented.
- Metadata parsing/read failures appear as `read_error`; absent ID3 headers produce missing-tag issues. Neither proves audio stream integrity; full decoding is a future option.
- Multiple MP3 comments: baseline chooses first COMM frame; language and description selection need a policy. FLAC tags may be multi-valued: baseline chooses first value.
- MP3 cover dimensions are not currently measured; minimum dimension rule only applies where dimensions are available (FLAC).
- FLAC dimensions are read from the embedded image header with Pillow, rather than the potentially zero or stale width and height declared in the PICTURE block. Valid 600×600 or 800×800 artwork with declared zero dimensions is measured correctly. Image headers that cannot be read safely yield `artwork_dimensions_unavailable` when minimum dimensions are enabled, rather than a misleading `small_artwork` issue. Pixel data is not fully decoded; this is not an image-integrity test.
- Duplicate track detection uses `DISCNUMBER` when present and disc-folder number otherwise. Disagreements produce `disc_number_mismatch` by default; the tag remains authoritative.
- Album root `library_root` holding tracks directly will group those tracks together. Non-disc nested folders are considered distinct albums.
- Current HTML output includes local paths. Do not publicly serve reports without evaluating privacy exposure.

## Configuration and cache upgrades

Configuration is validated before writable outputs are opened. Omitted options
inherit defaults; empty `required_tags: []` and `artwork: {required: false}`
explicitly disable those checks. Unknown keys, null sections, duplicate YAML keys,
invalid regexes and unsupported template syntax are errors. See `docs/RULES.md`.

An unversioned baseline database is migrated transactionally to schema 1 without
dropping tracks. Its parser-version marker starts at zero, causing a one-time
metadata refresh on the first successful upgraded scan. Future parser-version
changes also trigger metadata refresh; ordinary YAML edits do not. Back up an
existing database before upgrading; an older binary must not reuse the migrated
database. Unsupported future schema versions are refused.

Parser version 2 refreshes existing cached metadata once to correct FLAC artwork
dimensions. No database deletion or `--full-scan` is necessary; later unchanged
scans use the cache normally. Install updated dependencies with
`pip install -e '.[dev]'`, or rebuild the Docker image, before running the upgraded
scanner; Pillow is now a runtime dependency.

A cache is bound to the resolved library path, root device/inode and normalized
extension set. Scope changes are refused without deleting records. Use a separate
`--db` for a different library, changed mount identity or different extension set;
`--full-scan` refreshes metadata within the existing scope. Legacy caches are
adopted only when all stored paths fit the requested root and extensions.

## Report publication

`reports/index.html`, `issues.csv` and `report.json` remain the entry points with
the same contents/schema. They are links through `reports/current`, which is
atomically switched only after all three files are written and synced in a new
`reports/generations/scan-*` directory. Existing regular report files are migrated
without changing their contents until publication. Failed generation writes leave
the previous report set available.

Consumers reading multiple files should resolve `reports/current` once and read
all files from that generation. The HTML file is standalone; use `cp -L` when
exporting the entry-point link. Old generations are retained so pinned readers
remain valid; remove unneeded generations only while no scans/readers use them,
and keep the generation referenced by `current`. This retention uses disk space.

See `docs/ARCHITECTURE.md`, `docs/RULES.md`, `docs/ROADMAP.md`, and `AGENTS.md`.

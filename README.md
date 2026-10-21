# Music Library Validator — starter repository

Read-only, rule-based MP3/FLAC quality audit with SQLite metadata caching, folder-based albums, and static HTML/CSV/JSON reports. **Version 0.1.0 is a development baseline, not a production-hardened release.** The baseline executes real scans and basic rules; planned features and limitations are documented below.

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e '.[dev]'
# Edit config/rules.yaml: library_root: /absolute/path/to/music
music-validator scan --config config/rules.yaml --db data/library.sqlite --reports reports
pytest -q
```

## Docker / Synology

1. Edit `compose.yaml`, replacing `/volume1/music` with your music directory.
2. `mkdir -p data reports`; ensure both folders are writable by the chosen `PUID:PGID` (default 1000:1000).
3. `docker compose build && docker compose run --rm music-validator`
4. Open `reports/index.html` locally; CSV and JSON are alongside it.
5. Subsequent runs use SQLite cache. To rebuild metadata: `docker compose run --rm music-validator scan --config /config/rules.yaml --db /data/library.sqlite --reports /reports --full-scan`.

In Docker use `/music` in `rules.yaml`. Music is mounted `:ro`; SQLite and reports are writable volumes. No web server or port is exposed. Can be scheduled with Synology Task Scheduler or cron. The container starts only for a scan.

## Agreed requirements & decisions

- Audit existing MP3 and FLAC files, never rename, rewrite tags, or alter music automatically.
- Required fields include `title`, `artist`, `album`, `tracknumber`, `comment`; embedded art is mandatory by default. Configure rule choices in YAML.
- Support cross-tag relationships (example: `comment == artist + ' - ' + album`) and title/filename conventions; custom equality templates work today.
- Group albums by **directory**, not tags. Directories named `CD1`, `CD 02`, `Disc-3`, `Disk_04` are disc subfolders of their parent. An album with two discs is one report group, and track 1 on disc 1 and disc 2 are not duplicates.
- Report *albums needing attention*, with track-level explanations in expandable HTML sections and machine-readable CSV/JSON.
- SQLite caches parsed metadata by path, size and nanosecond mtime. New/changed files are re-read; removed files are purged after a successful traversal. **All cached entries are revalidated against current YAML rules every run**, so rule edits do not require a metadata rescan.
- Read-only scanner, one-shot Docker execution, no always-on service or opened port, no mandatory external services.
- Default timezone Europe/Warsaw in Compose; editable.

## Examples

Expected naming convention: `01 - Track title.mp3` or `01 - Track title.flac`. `filename.regex` checks the stem; `filename.match_template` can additionally compare it with metadata, for example `{tracknumber:02d} - {title}`. Avoid enabling this template until track number formats and character escaping are confirmed.

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
/music/Artist/Album/Bonus/01 - Demo.mp3      -> /music/Artist/Album/Bonus (separate)
```

## Implementation status

Implemented: recursive file enumeration, ID3 and FLAC Vorbis Comment normalization via Mutagen, MP3 APIC / FLAC Picture detection, album directory grouping, disc-folder fallback, missing field / cover / filename regex checks, optional filename template and custom equality rules, album/title and albumartist consistency options, duplicate `(disc, track)` detection, optional cover-hash consistency, SQLite incremental cache, read errors and three report formats.

Not implemented yet: MP3 cover image dimensions (APIC doesn't directly expose them; add Pillow); FLAC stream integrity check (`flac -t`); hashing music/audio data; resilient path rename detection; strict rule configuration schema; album directory for unconventional multi-disc names; semantic comparison of multi-valued tags; alternate disc numbering variants; JSON/YAML rule-expression language; scan history persistence (table exists, not populated); a persistent web UI; reproducible locked dependencies; structured logging and performance benchmarks.

## Verification of this starter archive

- `PYTHONPATH=src pytest -q`: 5 tests passed in the build environment.
- End-to-end fixture: first scan parsed an MP3 and produced all three reports; second scan reread 0 files using SQLite.
- `pip install -e .[dev]` and `docker compose build` were not verified here because the build environment cannot download dependencies. The Dockerfile and packaging should be exercised on your development machine.
- FLAC parsing has not yet been end-to-end tested with a real fixture; add one before production use.

## Safety and edge cases

- Files are **never changed**. SQLite and reports are written outside `/music`.
- Scanner skips symlinked audio files; symlinked directory traversal is not enabled. Mounts below `/music` and permission errors need deployment testing.
- In-progress scans update SQLite; an unexpected interruption may leave partially updated cache. Upgrade to transactional scan generations before relying on unattended production workflows.
- Cache's `(size, mtime_ns)` check can miss external edits that preserve both attributes; `--full-scan` forces metadata reread. No cryptographic change check is implemented.
- Corrupt/unreadable files appear as `read_error`; integrity testing of complete audio streams is a future option.
- Multiple MP3 comments: baseline chooses first COMM frame; language and description selection need a policy. FLAC tags may be multi-valued: baseline chooses first value.
- MP3 cover dimensions are not currently measured; minimum dimension rule only applies where dimensions are available (FLAC).
- Duplicate track detection uses `DISCNUMBER` when present and disc-folder number otherwise; if they disagree, no error is reported yet. A future rule should flag this.
- Album root `library_root` holding tracks directly will group those tracks together. Non-disc nested folders are considered distinct albums.
- Current HTML output includes local paths. Do not publicly serve reports without evaluating privacy exposure.

See `docs/ARCHITECTURE.md`, `docs/RULES.md`, `docs/ROADMAP.md`, and `AGENTS.md`.

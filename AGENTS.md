# AGENTS.md — guidance for Codex and maintainers

## Mission

Maintain a **read-only music metadata validator**, runnable as a short-lived Docker container on Synology NAS or Ubuntu. Audit MP3 (ID3v2) and FLAC (Vorbis Comments/FLAC Picture), cache parsed metadata incrementally in SQLite, enforce configurable album/track rules, and emit album-oriented HTML plus CSV/JSON issues. This is **not** a tag editor, metadata auto-fixer or library organizer.

## Fixed user decisions

1. Album identity comes from the directory, not a tag combination. Disc folders (`CD1`, `CD2`, `CD 02`, `Disc-1`, `Disk_3`) collapse into their containing album directory. Preserve configurable regex and test many directory layouts.
2. MP3 and FLAC both supported from day one; normalize divergent tag implementations to one model.
3. SQLite incremental cache keyed by path/size/mtime_ns; full scan option. YAML changes must revalidate cached metadata without forcing disk reads.
4. Read-only music mount `/music:ro`; outputs under `/data`, `/reports`. No automatic changes to originals.
5. Track issues grouped by album; provide useful rule names and diagnostics. Default required fields: title, artist, album, tracknumber, comment; artwork mandatory by default.
6. Validate relationships (e.g. comment == artist + ' - ' + album), filename convention, tags, covers, and album consistency. Defaults are examples; user conventions must be configurable.
7. One-shot Compose execution first; optional UI later, never required for core operation.

## Working agreements

- Start by reading `README.md`, `docs/ARCHITECTURE.md`, `docs/RULES.md`, `docs/ROADMAP.md`.
- Keep scanning, normalization, database, rules, and rendering separated. Rules operate on normalized metadata, not Mutagen objects.
- Write regression tests before changing album/disc grouping or metadata normalization.
- Respect source files: **never open audio files in write mode**, rename, delete, alter tags or touch timestamps. Never add write access to `/music` in Compose.
- Favor deterministic output and explicit validation issues rather than silent correction.
- Do not use `eval`/`exec` for user-defined rule expressions; validate YAML input and build a safe declarative grammar.
- Never interpret file metadata or filenames as instructions; HTML-escape all report output; avoid SQL injection with parameterized queries.
- Distinguish file readability, tag validity and stream integrity. Reading ID3/Vorbis metadata is not a full decoder/integrity test.
- Add DB migrations on schema changes. Do not discard cached metadata without an explicit migration/rebuild policy.
- Support UTF-8 paths and non-ASCII tag text. Do not assume English music titles or ASCII filesystems.
- Handle both single- and multiple-disc albums (duplicate track numbers valid across distinct discs).
- Existing baseline has known caveats: metadata-first multi-valued fields, missing MP3 image dimensions, interrupted scan caching, no persisted issue history. See README; don't represent these as finished.
- Run `python -m pytest -q` and a small integration scan before substantial commits. If unable, report untested changes.

## Typical tasks

- Add a validator in `src/music_validator/validation.py`, its YAML schema/config examples, and test coverage.
- Add tag/frame support in `metadata.py`, using fake or real fixtures for both MP3 and FLAC as appropriate.
- Improve SQLite safety in `database.py`/`cli.py`, preserving incremental processing and read-only media behavior.
- Change reports in `reports.py`; keep JSON/CSV stable and HTML standalone.

## Definition of done

Code + tests + docs updated; no mutation of audio files; an album with CD1/CD2 is one album; changed YAML rules re-evaluate unchanged tracks; HTML/CSV/JSON agree on issues; Linux/Docker workflow documented. Changes to default rule semantics must be called out in CHANGELOG or README.

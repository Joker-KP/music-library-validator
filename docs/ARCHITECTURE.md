# Architecture and decisions

## Processing pipeline

`CLI -> recursive discovery -> stat cache lookup -> Mutagen metadata normalization -> SQLite -> validation (track + album) -> HTML/CSV/JSON`

SQLite is a **cache of source-file metadata**, not the source of truth. Configuration changes cause validation of all indexed records on every run. There is no persistent issue table yet: reports are regenerated. `tracks` includes JSON metadata, album directory, size/mtime; `scans` is reserved for future scan history.

## Album identity

Filesystem directory is the default album key. Starting from a track's parent directory, remove terminal disc-folder components matching the configured case-insensitive regex `^(?:cd|disc|disk)[ _.-]*0*[1-9][0-9]*$`. `Artist/Album/CD1/track` and `Artist/Album/CD2/track` share `Artist/Album`. Unrecognized nested folders remain separate albums. Detect disc number from nearest recognized directory. Future: configurable patterns like `CD 1 of 2`, multi-disc sets with bonus folders, and explicit per-path overrides. Folder rename changes album identity.

## Metadata normalization

MP3: ID3 frames `TIT2` title, `TPE1` artist, `TALB` album, `TPE2` albumartist, `TRCK` tracknumber, `TPOS` discnumber, `COMM` comment, `APIC` cover. FLAC: Vorbis Comments, with key alias normalization; FLAC Picture blocks for cover. The normalized model stores fields as strings, plus cover SHA-256/dimensions where available. Handling multiple values, exotic tags and language-specific comments requires extension.

## Validation execution

Missing-tag, image presence/dimensions, filename regex and template rules run per track. Album consistency and duplicate disc/track run per album. Issues include album, path, rule ID, severity and text. Configured `album_rules` flags enable/disable checks. All rules are read-only.

## Operational model

Short-lived Python process invoked with Docker Compose. Mount `/music:ro`, writable `/data` and `/reports`. SQLite path stored on a persistent writable volume. Designed for Synology/Ubuntu. Do not run concurrent writers against one SQLite file. Future: WAL with ownership and locking, scan session transactions, structured trace logs, unit/integration and fixture-based tests.

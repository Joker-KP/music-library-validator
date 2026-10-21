# Architecture and decisions

## Processing pipeline

`validated config -> output preflight/ownership locks -> descriptor discovery -> stat cache lookup -> Mutagen normalization -> SQLite transaction -> validation (track + album) -> cache commit -> atomic HTML/CSV/JSON publication`

SQLite is a **cache of source-file metadata**, not the source of truth.
`config.py`, `safety.py`, `scanner.py`, `metadata.py`, `database.py`, `validation.py`
and `reports.py` separate configuration, output safety, discovery, normalization,
persistence, rules and rendering. `cli.py` coordinates them.

Configuration changes revalidate all indexed metadata each run. Grouping is
recomputed for cache hits; read failures are retried next run. Discovery sorts
one directory at a time, uses no library-wide path/seen set, and opens descendants
relative to read-only directory descriptors with `O_NOFOLLOW`. Traversal errors
propagate. Validation loads one album at a time; issue lists and rendered reports
still scale with issue count. This is not a full audio decoder/integrity check.

Schema 1 migrates the unversioned baseline without dropping metadata and adds
`parser_version`, `last_seen_scan` and `cache_settings`. Legacy parser version zero
causes a one-time refresh. Bump `PARSER_VERSION` when normalization changes require
rereading audio. Migrations use individual statements inside `BEGIN IMMEDIATE`,
without `executescript` implicit commits. A single transaction includes migration,
scope binding, metadata updates, successful scan counters and generation-based
pruning. Any discovery/validation failure rolls back. `scans` stores completed
cache-scan summaries; there is no issue-history/delta table.

Cache scope records the canonical root, root device/inode and extensions. Different
scopes and future schemas are refused. Legacy rows must all fit the requested
scope before adoption. Root identity changes require a separate database.

## Album identity

Filesystem directory is the album key. Starting from a track's parent directory,
remove terminal disc-folder components matching the configured case-insensitive
regex `^(?:cd|disc|disk)[ _.-]*0*[1-9][0-9]*$`. `Album/CD1/track` and
`Album/CD2/track` share `Album`. Unrecognized nested folders remain separate
albums. Detect disc number from the nearest matching ancestor, stopping at the
library root. Custom patterns use a named numeric `(?P<disc>...)` capture, or
exactly one digit sequence in the matched folder name. Ambiguous/missing/nonpositive
numbers cause a clear error and rollback. Outside-root paths are rejected.
Folder rename changes album identity. Bonus-folder policies and path overrides
remain future work.

## Metadata normalization

MP3: ID3 frames `TIT2` title, `TPE1` artist, `TALB` album, `TPE2` albumartist, `TRCK` tracknumber, `TPOS` discnumber, `COMM` comment, `APIC` cover. FLAC: Vorbis Comments, with key alias normalization; FLAC Picture blocks for cover. The normalized model stores fields as strings, plus cover SHA-256/dimensions where available. Handling multiple values, exotic tags and language-specific comments requires extension.

## Validation execution

Missing-tag, image presence/dimensions, filename regex and template rules run per track. Album consistency and duplicate disc/track run per album. Issues include album, path, rule ID, severity and text. Configured `album_rules` flags enable/disable checks. All rules are read-only.

## Operational model

Short-lived Linux process invoked with Docker Compose. Mount `/music:ro`, writable
`/data` and `/reports`. All output paths are checked before writes; symlink output
components, foreign report links, multiply linked database files and unsafe SQLite
sidecars are rejected. Owned output directories must not be modified concurrently
by other programs. Advisory locks on both the database and report directory reject
overlapping scans; SQLite also takes `BEGIN IMMEDIATE` with no busy wait.

Report files are immutable generations published through one atomic `current`
symlink replacement. Stable entry-point links retain the HTML/CSV/JSON interface.
Consumers pin `current` once for cross-file consistency. Prior generations are
retained. Files and generation directories are synced before publication. SQLite
and report publication are separate durability boundaries: publication failure
leaves a committed cache and the prior complete reports, and the CLI returns 2.
No web server or UI is required or implemented.

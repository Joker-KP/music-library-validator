# Architecture and decisions

## Processing pipeline

`validated config -> output preflight/ownership locks -> descriptor discovery -> stat cache lookup -> Mutagen normalization -> SQLite transaction -> validation (track + album) -> cache commit -> atomic HTML/CSV/JSON publication`

SQLite is a **cache of source-file metadata**, not the source of truth.
`config.py`, `safety.py`, `scanner.py`, `metadata.py`, `database.py`, `validation.py`
and `reports.py` separate configuration, output safety, discovery, normalization,
persistence, rules and rendering. `characters.py` shares the safe filename
allowlist grammar between configuration validation and rules. `custom_rules.py`
evaluates declarative conditions and template comparisons on normalized metadata.
`catalog.py` summarizes each album independently of its validation issues.
`cli.py` coordinates them.

Configuration changes revalidate all indexed metadata each run. Derived date/year
normalization runs both during parsing and on cached tags before validation:
preserve complete dates, derive a missing year from a leading four-digit date,
and use an explicit year as a missing-date fallback. This needs no schema or
parser-version change because existing cached date/year strings are sufficient.
Grouping is
recomputed for cache hits; read failures are retried next run. Discovery sorts
one directory at a time, uses no library-wide path/seen set, and opens descendants
relative to read-only directory descriptors with `O_NOFOLLOW`. Traversal errors
propagate. Discovery also totals recursive regular-file sizes in postorder,
retaining only totals for detected album directories; links are excluded. This
uses the same traversal, without rereading audio or opening sidecar contents.
Validation and catalog aggregation load one album at a time; retained summaries
scale with album count, and issue lists/reports scale with issue count. This is
not a full audio decoder/integrity check.

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
regex `^(?:.*[ _.-])?(?:cd|disc|disk)[ _.-]*0*(?P<disc>[1-9][0-9]*)$`.
`Album/CD1/track`, `Album/CD2/track` and `Album/Some Album Name - CD2/track`
share `Album`. A prefix must end with a space, underscore, dot or hyphen before
the disc marker. The named capture extracts only the final disc number, ignoring
numbers in the prefix. Unrecognized nested folders remain separate
albums. Detect disc number from the nearest matching ancestor, stopping at the
library root. Custom patterns use a named numeric `(?P<disc>...)` capture, or
exactly one digit sequence in the matched folder name. Ambiguous/missing/nonpositive
numbers cause a clear error and rollback. Outside-root paths are rejected.
Folder rename changes album identity. Bonus-folder policies and path overrides
remain future work.

## Metadata normalization

MP3: ID3 frames `TIT2` title, `TPE1` artist, `TALB` album, `TPE2` albumartist, `TRCK` tracknumber, `TPOS` discnumber, `COMM` comment, `APIC` cover. FLAC: Vorbis Comments, with key alias normalization; FLAC Picture blocks for cover. The normalized model stores fields as strings, plus cover SHA-256/dimensions where available. Handling multiple values, exotic tags and language-specific comments requires extension.

MP3 `TEXT`, `TOLY` and `TCMP` additionally map to `lyricist`, `origlyricist`,
and `compilation`. FLAC retains those comment fields and aliases
`originallyricist`/`original_lyricist` to `origlyricist` when not already set.
Parser version 3 refreshes earlier caches to include these normalized fields;
the SQLite schema remains 1. Compilation status is derived from normalized tag
strings during validation, rather than inferred from albumartist or cached rules.

Parser version 4 adds a top-level numeric `bitrate` (bits/s) from MP3 stream
headers; unavailable MPEG headers leave it null while preserving ID3 tags.
This reuses the scanner's read-only file descriptor, without full decoding.
Older parser versions refresh once; schema 1 and existing metadata rows are
retained. Bitrate is technical metadata, separate from normalized tag strings.

FLAC artwork dimensions come from the encoded image header read with Pillow,
not the FLAC PICTURE block's declared width/height (which may be zero or stale).
Only image headers are read; pixels are not loaded. Unreadable/unsupported headers
or image-size safety-limit failures normalize dimensions to null and include a
`dimension_error`; track tags and the hash of original picture bytes are retained.
Minimum-dimension rules emit `artwork_dimensions_unavailable` for that picture.
MP3 APIC dimension measurement remains deferred. Parser version 2 forces a
one-time metadata refresh to replace old cached zero dimensions; later parser
versions include this correction as well.

## Validation execution

Missing-tag, image presence/dimensions, filename regex and template rules run per track. Album consistency and duplicate disc/track run per album. Issues include album, path, rule ID, severity and text. Configured `album_rules` flags enable/disable checks. All rules are read-only.

The independent `filename.allowed_chars` rule checks the stem against a literal
allowlist with ASCII same-case letter/digit ranges. Punctuation is literal;
configuration is never used as an arbitrary regular expression. Empty/null
disables this rule. The default `[]0-9a-zA-Z -()` allows only brackets, ASCII
digits/letters, a space, hyphen and parentheses. Each offending track gets one
`filename_allowed_chars` issue listing unique disallowed characters in sorted
order with Unicode code points. These rules and grouping run on cached metadata
without a parser-version change or metadata reread.

Custom rules evaluate `when` first (AND of format, compilation, presence and
literal-substring selectors), then render `template.pattern` and compare with
`equals`, literal `contains`, or regex `fullmatch`. Every custom rule requires
a structured template; there is no legacy equality shorthand. Configuration restricts placeholders and validates
regex syntax with stand-in values; regex interpolation escapes metadata as
literal text. Evaluation errors become explicit issues. Configured pattern and
rendered-regex lengths are bounded at 4096. No `eval`, `exec` or arbitrary
expression language is used, and custom rules never access Mutagen objects.

## Operational model

Short-lived Linux process invoked with Docker Compose. Mount `/music:ro`, writable
`/data` and `/reports`. All output paths are checked before writes; symlink output
components, foreign report links, multiply linked database files and unsafe SQLite
sidecars are rejected. Owned output directories must not be modified concurrently
by other programs. Advisory locks on both the database and report directory reject
overlapping scans; SQLite also takes `BEGIN IMMEDIATE` with no busy wait.

Issue reports (`index.html`, `issues.csv`, `report.json`) and catalog reports
(`catalog.html`, `catalog.csv`, `catalog.json`) are rendered from the same
completed scan with the same timestamp. The catalog includes albums with no
issues, aggregates conflicting tag values deterministically, ranks FLAC above
MP3 and uses the minimum per-track MP3 bitrate. Unknown quality ranks below
known quality. All six output paths share preflight, ownership and publication
checks, including configuration-overwrite protection. HTML escapes tag values
and album-directory tooltip attributes. See README for catalog field semantics.

Report files are immutable generations published through one atomic `current`
symlink replacement. Stable entry-point links retain the HTML/CSV/JSON interface.
Consumers pin `current` once for cross-file consistency. Prior generations are
retained. Files and generation directories are synced before publication. SQLite
and report publication are separate durability boundaries: publication failure
leaves a committed cache and the prior complete reports, and the CLI returns 2.
No web server or UI is required or implemented.

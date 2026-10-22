# Architecture and decisions

## Processing pipeline

`validated config -> output preflight/ownership locks -> count-only descriptor discovery -> SQLite transaction -> processing discovery (cache/metadata + optional fingerprints) -> validation -> cache commit -> atomic HTML/CSV/JSON publication`

SQLite is a **cache of source-file metadata**, not the source of truth.
`config.py`, `safety.py`, `scanner.py`, `metadata.py`, `database.py`, `validation.py`
and `reports.py` separate configuration, output safety, discovery, normalization,
persistence, rules and rendering. `characters.py` shares the safe filename
allowlist grammar between configuration validation and rules. `custom_rules.py`
evaluates declarative conditions and template comparisons on normalized metadata.
`cross_album.py` projects matching identities and credit strings from normalized
metadata and compares one identity group at a time, independently of Mutagen.
`catalog.py` summarizes each album's metadata and rounds recursive folder sizes
to whole MB. Rendering adds consecutive row numbers and per-album counts from
the emitted validation issues, leaving zero counts blank in all catalog formats.
`cli.py` coordinates them.

The CLI performs two filesystem traversals. `scanner.count_files` streams the same
filtered, symlink-safe descriptor discovery without opening audio or retaining
paths; it runs before opening the cache transaction. `scanner.scan` then processes
each file's metadata and optional fingerprint cache in one discovery traversal,
including directory-size aggregation. A callback advances once per completed file,
including cache hits and explicit read/fingerprint errors. The count is an estimate:
the CLI reports changes between traversals rather than trusting an obsolete list.

`progress.py` owns display and time estimation on stderr. Scanning and audio rules
accept optional callbacks and do not print. Phase contexts distinguish counting,
file analysis, tag validation, cross-album rules, audio indexing/matching and report
publication. Terminal redraws are limited to five per second; nonterminal logs to
one line per ten seconds, plus starts/completions/failures. Completion is emitted
only on successful phase exit; interrupted/error phases end with a failed marker.
`--no-progress` disables display without changing scan/cache/report behavior.

Optional audio identity runs after metadata validation and before cache commit.
`fingerprints.py` invokes full-track Chromaprint algorithm 2 on a verified read-only
descriptor with a forced audio format. `scanner._scan_audio_track` independently checks
fingerprint signatures/backend versions, so enabling audio analysis on a populated
metadata cache does not reparse tags. It uses the same descriptor-based discovery
and pre/post read signatures; unsafe changes abort or produce explicit audio errors.
Fingerprint work is skipped when disabled or the field list is empty. A backend
version check occurs before writable output initialization. No online service is used.

`audio_identity.py` compares normalized raw fingerprint words with duration and
coverage gates, then emits tag conflicts. Its `landmarks` function provides audio
keys to `database.AudioIndex`, which builds connection-local SQLite projections
and indexes. At thresholds >=0.95, nine combinations of disjoint parts of adjacent
32-bit words retrieve candidates; every fourth pair is indexed and all pairs are
queried. Accepted pairs have >=32 overlapping frames and sufficiently many adjacent
two-bit-tolerant matches for this sampling to retain a candidate. Relaxed thresholds
use duration-filtered exhaustive candidates to avoid recall loss. Comparison votes
for alignments from full landmarks (or exhaustively searches offsets below 0.75),
and accepts only an alignment meeting both configured score and coverage thresholds.

Grouping is deterministic greedy complete-link: sorted paths, first compatible
group, all existing members must match. No similarity-chain union is performed.
Python retains path memberships, current comparisons and the current report group;
SQLite holds fingerprints and compared tag values. Common/similar audio and relaxed
thresholds can still require quadratic comparisons, and very repetitive fingerprints
can create many alignment votes. No large-library performance claim is made.
Groups are independent of directory albums; conflicts use the existing issue schema
and catalog counts. Local labels hash sorted paths and are not canonical audio IDs.

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
Album validation and catalog aggregation load one album at a time. When enabled,
cross-album validation makes a second pass over cached normalized metadata,
projecting only title, canonical artist, album/path and configured tag values
into a connection-local temporary SQLite table. SQLite sorts this projection;
Python retains only one matching identity group at a time, rather than all
tracks' metadata. The temporary table is discarded on connection close and
changes no persistent schema. Compared values are stored in a JSON mapping,
separately from identity/reference fields, so tag names never become SQL column
names or overwrite reference paths. Field-list, rule and alias changes never
force audio rereads. An empty field list skips this projection and comparison.
Retained summaries scale with album count, issue lists/reports with issue count,
and cross-album working memory with the largest matching group. This is not a
full audio decoder/integrity check.

Schema 1 migrates the unversioned baseline without dropping metadata and adds
`parser_version`, `last_seen_scan` and `cache_settings`. Legacy parser version zero
causes a one-time refresh. Bump `PARSER_VERSION` when normalization changes require
rereading audio. Migrations use individual statements inside `BEGIN IMMEDIATE`,
without `executescript` implicit commits. A single transaction includes migration,
scope binding, metadata updates, successful scan counters and generation-based
pruning. Any discovery/validation failure rolls back. `scans` stores completed
cache-scan summaries; there is no issue-history/delta table.

Schema 2 transactionally adds `audio_fingerprints` (path, size, mtime_ns,
backend/extraction version, decoded duration, raw fingerprint JSON, error), preserving
schema-1 metadata and parser version 5. Matching configuration is not stored in the
fingerprint signature: changing fields, severity, duration tolerance, coverage or
similarity revalidates cached audio without decoding. Changed/removed tracks prune
stale fingerprints even while disabled. New/stale/failed entries, backend-version
changes and full scans decode again. Fingerprint failures are separate from metadata
errors and do not discard valid tags. Audio updates and comparisons join the existing
scan transaction and publication guarantees. Neither partial fingerprints nor
nonzero decoder exits are treated as valid audio identities.

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

Parser version 5 maps MP3 ID3 `TCOM` to normalized `composer`, matching FLAC
`COMPOSER`. Older cache entries refresh once automatically; the SQLite schema
stays at 1. Later rule edits reuse the cached composer value.

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

Cross-album credit conflicts are evaluated inside the scan transaction, before
cache commit and report publication. They are sorted together with track/album
issues by album, path and rule. Failures roll back the scan and preserve prior
reports; catalog issue counts include these conflicts.

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

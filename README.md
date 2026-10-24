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

## Optional report server and scan controls

Run the browser interface against the same config, cache and report directory as
the CLI:

```bash
music-validator serve --config config/rules.yaml --db data/library.sqlite --reports reports
# Open http://127.0.0.1:8765
```

The server has no authentication and binds to loopback by default. To make it
available on your trusted LAN, pass `--host 0.0.0.0` and open
`http://<NAS-or-Ubuntu-address>:8765`. Change the port with `--port`.
No cloud services, JavaScript build, additional Python dependencies or Docker
socket access are required.

For Docker/Synology, use the optional Compose service; it inherits the scanner's
music/config read-only mounts, cache, reports and UID/GID:

```bash
docker compose --profile ui up -d --build report-server
# Available on the Docker host at http://127.0.0.1:8765
# For LAN access instead:
UI_BIND_ADDRESS=0.0.0.0 docker compose --profile ui up -d report-server
# Stop the optional service:
docker compose --profile ui stop report-server
```

`UI_PORT` changes the published host port. The original
`docker compose run --rm music-validator` and all local CLI commands still work.
Only one scan can own a given cache/report destination at a time. The interface
detects occupied locks and reports an external scan; it cannot monitor or cancel
a CLI job. Concurrent attempts still pass through the scanner's existing locks,
including races during preflight/counting, so a worker may fail with a lock error.

The scan form offers configured audit, integrity-only and audio-identity-only
analysis. All start incrementally. Optional refresh controls correspond to
`--full-scan`, `--recheck-integrity` and `--recheck-identity`; configured-audit
rechecks require those features to be enabled in YAML. The same configuration,
tool preflight, validation and read-only scan pipeline is used in both entry points.
There is no queue: a second server job is rejected while one is active.

The current/last job panel shows its operation, phase counts, phase elapsed time
and ETA, job start/finish times, bounded log, errors, cancel and run-again actions.
Closing a browser tab does not stop its job. A server shutdown cancels its worker
and audio subprocess group, allowing cleanup before forced termination after
eight seconds. Cancellation before cache commit rolls back; cancellation/failure
after commit may leave the cache updated while the previous reports remain.
Successful scans with findings are labelled completed with findings, rather than
failed. Job state and the last 100 log lines are held in memory for this server
session; there is no persisted job/issue history or restart recovery.

Four report views provide an overview, searchable/sortable album catalog and
track issues, per-tool integrity coverage, and matching-audio groups with compared
tag values. Search/filtering and pagination run on the server (50 rows per page,
20 audio groups); browser memory does not need the entire report. Full parsed
JSON snapshots are held in a bounded server cache of up to six generations, so
server memory still scales with report size. Normal reports expose album
summaries and issues, not a complete tag inspector for every track.

Reports are read from published immutable generations, without querying the live
SQLite database or reading music during browsing. Each family shows its own
generation time. Result pages and full HTML/CSV/JSON downloads pin one generation;
new CLI or UI results produce a refresh notice instead of mixing old and new
pages. Downloads remain complete exports regardless of browser filters. Missing
reports show an empty state. Previously published reports remain available during
a scan. Existing standalone HTML exports are unchanged and need no server.

Configuration editing, acknowledgments, trends and historical comparisons are
outside this interface. It never offers music modification or serves arbitrary
music/config/cache paths. Metadata is rendered as text; state-changing HTTP calls
require same-origin JSON and the `X-Music-Validator: 1` header. There is no sign-in.
The unauthenticated service is intended for local or trusted-LAN access.

### Fresh start after changing libraries

Open **Library maintenance** below the scan controls, read the warning, type
`FRESH START`, and select **Archive and start fresh**. This archives the active
SQLite cache (metadata, fingerprints, integrity results, inventory and scope
binding), its SQLite sidecars, and all generated configured/integrity/identity
reports. It clears the session's last job and report snapshots without starting
an analysis. Start a new analysis when ready; it creates a new cache bound to the
currently configured library. Unlike `--full-scan`, this allows a different library
scope. Existing CLI commands continue to work with the same database path.

The archive locations are shown after completion. With the default Docker paths,
they are `/data/previous-data-TIMESTAMP-ID/` and
`/reports/previous-reports-TIMESTAMP-ID/`; timestamps are UTC and names are unique.
For a custom `--db`, the data archive sits beside that database. Reports retain
their relative `current`/generation links and specialist subdirectories inside
the archive. Empty state does not create empty archive folders. Existing archives,
unrelated files, configuration and music remain in place. The action archives only
the selected cache and managed report outputs, not every file in `/data`.

The action refuses active server jobs and takes the actual cache and all three
report-family locks, including for CLI scans. Lock files stay in place to preserve
coordination with other processes. This server finishes in-flight report reads
before archiving; old generation URLs stop working after a fresh start. Other
browser tabs clear their old results on polling. Archives remain on disk until
you manage them manually; the UI does not provide history or restoration tools.

Ordinary I/O failures reverse completed moves, with recovery paths reported if
restoration also fails. Renames occur within each volume; the two archive roots
are not one filesystem transaction. An abrupt machine shutdown can leave a partial
archive: inspect the active outputs and timestamped archives before scanning again.

## Scan progress

Every scan first walks the directory tree to count supported audio files, without
opening audio, reading tags, decoding or changing the cache. This pass also reads
and validates directory-specific `rules.yaml` patches before creating writable
outputs. A second filesystem
pass processes metadata and, when enabled, integrity checks and fingerprints together. Unchanged cached
tracks count toward progress because they are still checked and revalidated.

Progress appears on stderr with the file count, percentage, elapsed time and an
estimated remaining time. Terminals update one bar in place; Docker/cron logs get
plain lines at most every ten seconds, plus phase start/completion. Phase names
are padded with spaces so progress bars start in the same column. The counting
phase shows the number found so far. Later phases show tag validation, cross-album
rules, optional audio indexing/matching and report publication, so finishing file
discovery does not imply the whole analysis has finished. A phase reaches 100%
only after it succeeds; failures and interruptions are marked failed. ETA is an
estimate from completed files in that phase, not a prediction for the whole run.

Files added/removed between counting and processing can change the total; the CLI
reports the difference and finishes with the actual processed count. Both passes
skip symlinks and use the same extension filters. This adds one directory/stat
traversal to metadata-only scans, without retaining a library-wide path list.
Use `music-validator scan ... --no-progress` to suppress progress; the counting
pass and normal stdout summary remain. The same flag works in Compose commands.

## Docker / Synology

1. Edit `compose.yaml`, replacing `/volume1/music` with your music directory.
2. `mkdir -p data reports`; ensure both folders are writable by the chosen `PUID:PGID` (default 1000:1000).
3. `docker compose build && docker compose run --rm music-validator`
4. Open `reports/index.html` for issues or `reports/catalog.html` for all albums;
   each has CSV and JSON exports alongside it.
5. Subsequent runs use SQLite cache. To rebuild metadata: `docker compose run --rm music-validator scan --config /config/rules.yaml --db /data/library.sqlite --reports /reports --full-scan`.

In Docker use `/music` in `rules.yaml`. Music is mounted `:ro`; SQLite and reports are writable volumes. The default service exposes no port and starts only for a scan. It can be scheduled with Synology Task Scheduler or cron. An optional report server is described below.

The Dockerfile installs system packages and creates the runtime user before copying
application files. Iterative source edits therefore reuse that layer and rebuild
only the application copy/install steps. Keep Docker's build cache enabled for
normal development builds.

The scanner targets Linux, including Ubuntu and Synology, and uses directory
descriptors, `O_NOFOLLOW` and `flock`. Keep database and report directories owned
by the scanning user and outside the music library. Output directories, SQLite
files/sidecars and lock files must not be symlinks; database files must have one
hard link. The report directory must support symbolic links and atomic rename.
After read-only preflight, scans using either an occupied database or an occupied
report directory fail at lock acquisition with exit code 2.

## Agreed requirements & decisions

- Audit existing MP3 and FLAC files, never rename, rewrite tags, or alter music automatically.
- Required fields include `title`, `artist`, `album`, `tracknumber`, `comment`; embedded art is mandatory by default. Configure rule choices in YAML.
- Support conditional cross-tag relationships and title/filename conventions through custom templates with `equals`, `regex` and `contains` operators.
- Group albums by **directory**, not tags. Directories named `CD1`, `CD 02`, `Disc-3`, `Disk_04`, or ending with a separated disc marker such as `Some Album Name - CD2`, are disc subfolders of their parent. An album with two discs is one report group, and track 1 on disc 1 and disc 2 are not duplicates.
- Report *albums needing attention*, with track-level explanations in expandable HTML sections and machine-readable CSV/JSON.
- SQLite caches parsed metadata by path, size and nanosecond mtime. New/changed files are re-read; removed files are purged after a successful traversal. **All cached entries are revalidated against current YAML rules every run**, so rule edits do not require a metadata rescan.
- Album grouping is recalculated even on metadata cache hits. Cached read errors are retried on the next scan. Complete scans persist summary counters in SQLite.
- Read-only scanner and one-shot Docker execution by default; optional report server, no mandatory external services.
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
    template:
      operator: equals
      pattern: '{artist} - {album}'
    severity: warning
```

Add `year` to `required_tags` to require a release year. MP3 `TDRC`/legacy
`TYER` and FLAC `DATE` expose a normalized `year` from a leading four-digit
year, while preserving the complete `date`. An explicit FLAC `YEAR` takes
precedence and also supplies `date` if it is missing. This works with existing
cached dates without a full scan. Required fields check presence, not calendar
validity; see `docs/RULES.md` for supported date forms.

To require a lyricist credit only when the lyricist tag is present:

```yaml
custom_rules:
  - id: comment_lyricist
    when: {field: lyricist, is_present: true}
    field: comment
    template:
      operator: contains
      pattern: 'słowa: {lyricist}'
    severity: warning
```

For lyricist `Jan Łódzki`, a comment containing `słowa: Jan Łódzki` anywhere
passes. Missing/blank lyricist skips the rule. Other available conditions are
`when: {format: flac}`, `when: {is_compilation: true}`,
`when: {field: origlyricist, is_present: true}` and
`when: {field: title, contains: some-string}`. Multiple selectors in one `when`
mapping all must match. `equals` compares the entire rendered pattern literally;
`contains` requires the entire rendered substring; `regex` matches the whole field.
Tag values substituted into regex patterns are escaped as literal text. Double
regex quantifier braces in templates, e.g. `\d{{2}}` renders as `\d{2}`.
Every custom rule requires `template.operator` and `template.pattern`.
The removed `equals_template` key and `equal` operator are rejected; migrate
existing rules to `template: {operator: equals, pattern: '...'}`.
Full details are in `docs/RULES.md`.

Examples of grouping:

```text
/music/Artist/Album/01 - Intro.flac          -> /music/Artist/Album
/music/Artist/Album/CD1/01 - Intro.flac      -> /music/Artist/Album
/music/Artist/Album/CD2/01 - Outro.flac      -> /music/Artist/Album
/music/Artist/Album/Album - CD2/01.flac     -> /music/Artist/Album
/music/Artist/Album/Bonus/01 - Demo.mp3      -> /music/Artist/Album/Bonus (separate)
```

## Folder-specific rules

Place a UTF-8 `rules.yaml` inside the library to patch checks for that directory
and all descendants. Patches accumulate from `/music` toward each track; deeper
patches override only supplied settings, and sibling folders stay independent.
Directories without audio can provide patches for albums below them.

```yaml
# /music/Artist/Album/rules.yaml
added_required_tags: [composer]
removed_required_tags: [year]
removed_custom_rules: [comment_has_origlyricist]
artwork:
  required: false
album_rules:
  consistent_artwork: false
```

`added_custom_rules` accepts complete definitions using the main `custom_rules`
syntax. To replace an inherited rule, remove and add its ID in the same patch.
`artwork` and `album_rules` override individual keys; omitted keys stay inherited.
Optional artwork still has its dimensions checked when present: set both
`min_width: null` and `min_height: null` to disable dimension checks too.

Track settings can differ between CD1/CD2. Album checks always use settings at
the canonical album directory and compare all its discs; `album_rules` inside a
collapsed disc directory is rejected with the proper album location.
Filename, cross-album, integrity and identity settings remain global. Dedicated
audio modes validate local YAML but skip its metadata checks as usual.

Local edits, additions and deletions revalidate cached metadata without rereading
unchanged audio. Invalid patches identify their source file and fail before
cache/report writes. Detected mid-scan changes abort and preserve previous reports.
No audio, database-schema or Compose mount changes are needed.
See [the full merge rules](docs/RULES.md#folder-specific-rule-patches) and
[example patch](config/local-rules.example.yaml).

## Cross-album credit consistency

Tracks with the same `title` and `artist` in different album directories are
checked for consistent `composer`, `lyricist` and `origlyricist` values. This is
enabled by default. Each conflicting field produces a warning for every matched
copy, with the current value on its own line and each compared value/reference
path on a separate line. These issues appear
under the affected albums in HTML/CSV/JSON and count toward catalog issue totals.

Use artist alias groups when a band or performer has multiple names:

```yaml
cross_album_rules:
  enabled: true
  severity: warning
  fields: [composer, lyricist, origlyricist]
  artist_alias_groups:
    - [Wolna Grupa Bukowina, Wojciech Bellon]
    - [U Pana Boga za Piecem, Sylwek Szweda, Towarzstwo Wzajemnej Adoracji]
```

Names in one group are equivalent for this check; other rules and original tags
keep their existing values. Groups must not overlap. Matching and credit
comparisons trim surrounding whitespace and remain case-sensitive. Missing and
blank credits are equivalent, but differ from filled credits. Read errors and
tracks without a nonblank title/artist are skipped. Copies confined to one album
are outside this check, including its collapsed disc folders.

Set `fields` to the normalized tag names you want compared; the list replaces
the default three fields, so `fields: [composer]` checks only composer.
Other exposed tags, such as `genre` or `comment`, can be included. A field must
already be exposed by the metadata reader; this setting does not add ID3 frame
support. Use `fields: []` or `enabled: false` to disable comparisons, or
`severity: info` / `error` to change the severity.
Field-list and alias/rule edits revalidate the SQLite metadata cache without audio
rereads; this feature needs no schema migration or parser-version refresh.
The existing first-value policy for multi-valued tags still applies.

## Implementation status

Audio-based tag consistency is also available as an opt-in check; see below.

Implemented: recursive file enumeration, ID3 and FLAC Vorbis Comment normalization via Mutagen, MP3 APIC / FLAC Picture detection, album directory grouping with disc suffixes, disc-folder fallback, missing field / cover / filename regex and allowed-character checks, optional filename template and conditional custom rules, lyricist/original-lyricist/compilation normalization, album/title and albumartist consistency options, duplicate `(disc, track)` detection, optional cover-hash consistency, SQLite incremental cache, read errors, and issue/catalog reports in HTML, CSV and JSON.

Not implemented yet: externally supplied audio checksum manifests; resilient path rename detection; semantic comparison of multi-valued tags; nonnumeric disc numbering; a richer declarative rule-expression language; persisted database issue history/deltas; reproducible locked dependencies; general structured logging and performance benchmarks.

## Audio integrity (optional)

Enable full-stream checks separately from metadata validation and audio matching:

```yaml
audio_integrity:
  enabled: true                 # Default: false
  flac:
    native_test: true
  mp3:
    structure_check: true
    decode_check: true
  timeout_seconds: 300          # Positive, finite deadline per tool/file
```

On Ubuntu install `sudo apt-get install flac mp3val ffmpeg`; the Docker image
includes all three. Only tools selected for configured extensions are required.
Missing/nonfunctional tools fail before cache/report output initialization.

FLAC runs `flac --test --silent`, decoding the complete stream, checking frame
integrity and comparing decoded audio with its stored MD5 when available. A missing
MD5 produces `flac_checksum_unavailable` (warning), not a corruption error. A
mismatch produces `flac_checksum_mismatch` (error). See the
[FLAC test-mode documentation](https://xiph.org/flac/documentation_tools_flac.html).

MP3 runs MP3val in scan mode and FFmpeg with `-xerror`, decoding only the first
audio stream to a null output; embedded artwork is excluded. MP3val warnings such
as resynchronization, junk and possible truncation become `mp3_structure_warning`.
Possible truncation remains ambiguous: the tool can also be seeing trailing junk.
Invalid MPEG format is `mp3_structure_error`; confirmed decoder failures are
`audio_decode_error` (errors). MP3val's missing-tag warning is left to metadata
rules. Duration estimation alone is not an integrity issue. Other decoder warnings
remain `audio_integrity_warning`. MP3val checks frame structure, not the encoded
audio inside each frame, which is why both MP3 checks are recommended. See the
[MP3val manual](https://mp3val.sourceforge.net/docs/manual.html).

Timeouts, crashes, I/O failures, unexpected tool failures and excessive diagnostics
become `audio_integrity_incomplete` (warning), indicating no completed verdict.
Incomplete checks retry next scan. All findings appear in the same album-oriented
HTML/CSV/JSON reports and catalog issue counts as tag issues. Successful metadata
parsing and fingerprinting alone still do not establish integrity. Confirmed
integrity errors exclude a track from audio matching; warnings alone do not.

Checks use verified read-only file descriptors and never enable repair options,
create decoded audio, edit tags or alter timestamps. Integrity work runs during
the second filesystem pass, before advancing that file's progress. Fingerprinting
uses a separate decoder, so enabling both features adds CPU and I/O work.

SQLite schema 3 adds independent results per path/tool, retaining metadata and
fingerprint caches. Unchanged completed results, including media errors, are
reused; enabling another tool runs only the missing checks. File signatures,
tool/profile version changes and `--full-scan` invalidate affected results.
Changing tag rules or the timeout does not invalidate completed integrity results.
Disabled checks do not contribute findings. Removed/changed files prune stale
integrity results even when checking is disabled. Force only integrity checks with:

```bash
music-validator scan --config config/rules.yaml --db data/library.sqlite --reports reports --recheck-integrity
# Docker, using the same enabled configuration:
docker compose run --rm music-validator scan --config /config/rules.yaml --db /data/library.sqlite --reports /reports --recheck-integrity
```

The flag requires `audio_integrity.enabled: true`. Schedule occasional forced
checks to detect storage damage that preserves file size and nanosecond mtime;
ordinary incremental scans cannot detect such changes. Passing checks cannot
prove a recording is complete, audibly pristine or identical to an external master;
MP3 has no universally available whole-audio checksum, and missing complete frames
may still leave a decodable stream.

## Dedicated audio analyses

Run just one audio analysis without editing YAML enable settings:

```bash
music-validator scan --config config/rules.yaml --db data/library.sqlite --reports reports --audio-integrity-only
music-validator scan --config config/rules.yaml --db data/library.sqlite --reports reports --audio-identity-only
```

The flags are mutually exclusive. Each enables its feature for this invocation
and disables the other feature. Detailed YAML settings still apply: integrity tool
choices/timeouts and identity fields/severity/matching thresholds. A dedicated
integrity invocation with no applicable checks, or identity with `fields: []`,
fails before creating outputs. YAML itself is never rewritten. Configuration is
still validated in full, including sections unrelated to the selected mode.

Integrity-only performs discovery and selected stream checks without parsing tags,
inspecting artwork, running metadata rules, fingerprinting or creating an album
catalog. Identity-only runs fingerprinting/matching and configured tag comparisons;
it reads metadata only for new, changed, failed or outdated cached entries, using
the existing metadata reader. It skips integrity tools and unrelated validation
rules/catalog generation. Matching file-signature cached integrity errors exclude
known-bad tracks and produce `audio_identity_skipped_integrity` warnings with
per-track reasons; old-signature failures are ignored. Recheck integrity to
reassess exclusions. Cached integrity warnings alone do not exclude tracks.

Both modes keep the count-first pass, processing progress and incremental cache.
They share the same SQLite database, so later normal scans can reuse their work;
the database lock rejects overlapping modes. Directory-based album grouping,
read-only music access and output-path checks apply to every mode.

Each dedicated run updates only its own report family:

```text
reports/index.html                   # Normal configured audit (+ issue exports/catalog)
reports/integrity-report/index.html   # Integrity only
reports/identity-report/index.html    # Identity only
```

Each dedicated directory has `issues.csv`, `report.json`, its own `current` link
and immutable generations. The normal audit and catalog are preserved, as is the
other dedicated report. Specialist HTML/JSON includes effective settings and
coverage statistics. Integrity lists per-file/tool statuses, last check times,
cached/executed results and files without applicable checks. Identity lists track
statuses, skipped/error reasons, fingerprint attempts/successes/reuse and all
matched groups, including consistent groups, with minimum pair similarity/coverage,
compared tag values and conflicting fields. CSV retains the standard issue columns;
HTML/CSV/JSON contain the same issues. Per-track details stream from temporary
SQLite tables rather than accumulating another full-library metadata list.

Selecting an operation does not force it to reread unchanged audio. Add:

```bash
# Repeat selected integrity checks, retaining metadata and fingerprints:
music-validator scan --config config/rules.yaml --db data/library.sqlite --reports reports --audio-integrity-only --recheck-integrity
# Re-extract fingerprints, retaining current metadata:
music-validator scan --config config/rules.yaml --db data/library.sqlite --reports reports --audio-identity-only --recheck-identity
# Refresh all data needed by identity analysis, including metadata:
music-validator scan --config config/rules.yaml --db data/library.sqlite --reports reports --audio-identity-only --full-scan
```

`--full-scan` refreshes only data needed by the selected mode: integrity-only still
does not parse metadata. Recheck flags for an excluded operation are errors.
Without an `--only` flag, the recheck flags require their feature to be enabled
and retain the normal YAML-driven audit/report behavior. Matching always runs
again with current settings, even when fingerprints are reused. Periodic forced
integrity checks remain necessary for storage changes preserving size/mtime.

For Docker, prepend `docker compose run --rm music-validator` to `scan`, and use
`--config /config/rules.yaml --db /data/library.sqlite --reports /reports` with
the same mode/recheck flags. All required tools are in the rebuilt image.

## Audio-based tag consistency (optional)

Match copies of the same recording using audio alone, then compare selected tags.
Unlike the title/artist cross-album rule, this can find different or missing titles
and artists, and includes copies within the same album and across albums. Album
identity still comes from directories; audio matching never merges catalog albums.

```yaml
audio_identity_rules:
  enabled: true
  fields: [title, artist, composer]
  severity: warning
  matching:
    similarity_threshold: 0.95
    duration_tolerance_seconds: 2.0
    minimum_coverage: 0.90
```

The shipped configuration uses these settings with `enabled: false`, so normal
metadata scans do not decode audio or require extra local tools. Set true to opt
in. For Ubuntu, install `sudo apt-get install libchromaprint-tools`; Docker images
include `fpcalc` and its decoder libraries after rebuilding. No AcoustID account,
API key, network calls, uploads or tag writes are involved. The first enabled scan
decodes each track in full, rather than fpcalc's default first 120 seconds. Expect
extra CPU and I/O on a NAS. Subsequent unchanged scans reuse fingerprints in SQLite.

Chromaprint fingerprints tolerate lossy encoding differences but are not universal
recording IDs. We compare aligned fingerprint sequences for similarity, rather than
requiring identical hashes. The score is the fraction of aligned 32-bit fingerprint
words within two bit errors; 0.95 is not a 95% probability. Coverage is the overlap
divided by the longer fingerprint sequence. Both thresholds and the duration
tolerance must pass for **every pair** in a group. This prevents A–B–C similarity
chains from merging A and C when they do not match directly.

The defaults are conservative starting points, verified with synthetic FLAC and
gapless MP3 encodings. Files without encoder-delay metadata, added silence, edits,
remasters or other performances may need different settings or remain separate.
Tune against representative known matches and known different recordings before
relying on the results. This identifies recordings, not all performances of a song.

Conflicts produce `audio_identity_<field>` issues for every group member, with a
local group label, minimum accepted pair scores, the current value and references
for each distinct value. They appear in the existing HTML/CSV/JSON reports and
catalog issue counts. Tag comparison trims outer whitespace, remains case-sensitive,
and treats absent/blank values as equivalent. The first-value tag policy still
applies. Group labels are deterministic for the same paths/membership, not permanent
IDs across renames or regrouping. No copy is treated as authoritative.

Matching/field/severity edits regroup cached fingerprints without decoding audio.
New or changed files, failed fingerprints, decoder/extraction-version changes and
`--full-scan` trigger decoding. Empty `fields: []` also disables this feature.
Schema 2 adds an independent fingerprint cache without discarding metadata or
changing its parser version; enabling the feature on an existing cache needs no
metadata refresh. Removed/changed paths invalidate old fingerprints even when
audio matching is disabled.

Tracks shorter than 10 seconds, fingerprints with fewer than 32 frames or fewer
than eight distinct words, and decoding failures produce `audio_fingerprint_error`
warnings, separate from metadata `read_error`. They are excluded from matching
and retried next run. Extraction has a 300-second per-track timeout and a maximum
24-hour track length. Duration comes from decoded samples, including FLAC with
unknown header duration. Successful fingerprinting is not an audio-integrity test.

At similarity thresholds of 0.95 or higher, an indexed audio-landmark search and
duration filtering find candidates. Lower thresholds use all duration-compatible
candidates to preserve recall and can be much slower. SQLite holds the temporary
search projection; Python retains group memberships and current comparisons.
Large-library CPU/disk use and Synology deployment still need field testing.
Details and bounds are in `docs/RULES.md`.

The implementation calls [Chromaprint's fpcalc](https://acoustid.org/chromaprint)
directly with raw JSON output. Its local comparison uses the same two-bit word
tolerance as [pyacoustid](https://github.com/beetbox/pyacoustid), with explicit
duration/coverage checks and conservative grouping; pyacoustid and its HTTP client
are not required dependencies.

## Verification

After installing `.[dev]`, run the same checks as GitHub Actions:

```bash
python -m pytest -q
ruff check src tests
ruff format --check src tests
```

Use `ruff format src tests` to apply formatting before committing.
Tests include real synthetic
MP3 and FLAC fixtures and subprocess CLI scans, Unicode metadata, cache reuse,
YAML-only changes, custom disc patterns, migration, interruption, traversal/read
failures, concurrent ownership, unsafe outputs, and report publication failures.
Fixture audio is copied to temporary directories. Most tests need no FFmpeg;
the real audio-identity integration test creates synthetic MP3/FLAC with FFmpeg
and requires `fpcalc`. Install `libchromaprint-tools ffmpeg` to run that test;
it is skipped when either is absent. CI installs both tools.
Source content hashes, sizes and modification times are checked across scans.

## Safety and edge cases

- Files are **never changed**. SQLite and reports are written outside `/music`.
- Scanner skips symlinked files and directories. Files are opened read-only relative to their directory descriptor without following links, and their stat signatures are checked before/after metadata parsing. Traversal/stat failures abort the scan and preserve the previous cache and reports. Descendant mount points are traversed; actual NAS mount behavior still needs deployment testing.
- Metadata changes and pruning share one SQLite transaction. An interrupted discovery/validation rolls back the entire scan. The cache commits before report publication; a report-write failure can leave an updated cache with the previous complete report set. Rerunning safely revalidates the cache and publishes reports.
- Cache's `(size, mtime_ns)` check can miss external edits that preserve both attributes; `--full-scan` forces metadata reread. No cryptographic change check is implemented.
- Metadata parsing/read failures appear as `read_error`; absent ID3 headers produce missing-tag issues. Neither proves audio stream integrity; enable the separate integrity checks for full decoding.
- Multiple MP3 comments: baseline chooses first COMM frame; language and description selection need a policy. FLAC tags may be multi-valued: baseline chooses first value.
- MP3 APIC and FLAC Picture dimensions are read from every embedded image header with Pillow. Each dimension below its configured `artwork.min_width` or `min_height` produces `small_artwork`; equality passes, and 0 or null disables that dimension check. FLAC's potentially zero or stale declared dimensions are ignored. Image headers that cannot be read safely yield `artwork_dimensions_unavailable` when minimum dimensions are enabled. Pixel data is not fully decoded; this is not an image-integrity test.
- Duplicate track detection uses `DISCNUMBER` when present and disc-folder number otherwise. Disagreements produce `disc_number_mismatch` by default; the tag remains authoritative.
- Album root `library_root` holding tracks directly will group those tracks together. Non-disc nested folders are considered distinct albums.
- Current HTML output includes local paths. Do not publicly serve reports without evaluating privacy exposure.

## Configuration and cache upgrades

Configuration is validated before writable outputs are opened. Omitted options
inherit defaults; empty `required_tags: []` and `artwork: {required: false}`
explicitly disable those checks. Unknown keys, null sections, duplicate YAML keys,
invalid regexes and unsupported template syntax are errors. See `docs/RULES.md`.

An unversioned baseline database is migrated transactionally through schemas 1–3 to
schema 4 without dropping tracks. Its parser-version marker starts at zero, causing a one-time
metadata refresh on the first successful upgraded scan. Future parser-version
changes also trigger metadata refresh; ordinary YAML edits do not. Back up an
existing database before upgrading; an older binary must not reuse the migrated
database. Unsupported future schema versions are refused. Schema-1 caches upgrade
to schema 2 by adding the audio fingerprint table; that upgrade alone does not
refresh tag metadata or require audio decoding while the feature is disabled.
Schema-2 caches upgrade to schema 3 by adding per-tool integrity results; metadata
and fingerprints are preserved, without changing parser version 5.
Schema 4 separates the current file inventory (`files`) from cached metadata
(`tracks`) and records the execution mode on scans. Inventory starts from existing
metadata rows. Integrity-only scans can update file signatures without marking
old tags fresh; subsequent metadata/identity scans reparse changed entries.
Removed files prune all caches after successful discovery. Changed-file metadata
may remain cached but is excluded until refreshed. Existing fingerprints and
integrity results retain their own signature/version checks.

Parser version 3 refreshes older cached metadata once to include MP3 lyricist,
original lyricist and compilation tags, and FLAC original-lyricist aliases.
Parser version 4 adds MP3 stream bitrate for catalog quality and refreshes older
entries once; subsequent scans reuse it. Parser version 5 adds MP3 `TCOM` as
`composer`, matching FLAC `COMPOSER`, and refreshes older entries once so
composer rules also work with existing libraries. Parser version 6 measures MP3 APIC
artwork dimensions, so minimum-size checks also apply to MP3s. Older metadata
entries refresh automatically once on the next configured or identity scan.
Parser version 2 introduced corrected FLAC artwork dimensions. No database
deletion or `--full-scan` is necessary; later unchanged
scans use the cache normally. Install updated dependencies with
`pip install -e '.[dev]'`, or rebuild the Docker image, before running the upgraded
scanner; Pillow is now a runtime dependency.

Once metadata has refreshed, edits to custom-rule conditions, patterns and
operators revalidate cached tracks without rereading music files.

A cache is bound to the resolved library path, root device/inode and normalized
extension set. Scope changes are refused without deleting records. Use a separate
`--db` for a different library, changed mount identity or different extension set;
`--full-scan` refreshes metadata within the existing scope. Legacy caches are
adopted only when all stored paths fit the requested root and extensions.

## Report publication

`reports/index.html`, `issues.csv` and `report.json` remain the issue-report entry
points with the same contents/schema. `catalog.html`, `catalog.csv` and
`catalog.json` contain all detected albums, including those without issues.
All six are links through `reports/current`, which is
atomically switched only after all six files are written and synced in a new
`reports/generations/scan-*` directory. Existing regular report files are migrated
without changing their contents until publication. Failed generation writes leave
the previous report set available.

Consumers reading multiple files should resolve `reports/current` once and read
all files from that generation. The HTML file is standalone; use `cp -L` when
exporting the entry-point link. Old generations are retained so pinned readers
remain valid; remove unneeded generations only while no scans/readers use them,
and keep the generation referenced by `current`. This retention uses disk space.

## Album catalog

Every scan also creates a standalone `reports/catalog.html` table with these
columns: No, album artist, year, album, quality, folder size (MB), total number of
tracks, Number of issues, Parent folder. Disc folders share one album row,
using the same directory identity as validation. Rows are ordered by album directory,
independently of tag values.

- `No` numbers album rows consecutively from 1 in that directory order.
- Album artist uses `albumartist`, falling back to `artist` for each track.
  Missing artist/year values display `Unknown`; missing album names fall back
  to the album directory name. Distinct values within one album are sorted and
  joined with `; ` so inconsistent tags remain visible.
- Quality is the worst track quality in the album: FLAC ranks above MP3, then
  MP3 tracks compare by bitrate (e.g. `MP3 320`, `MP3 128`). Bitrates are rounded
  to the nearest kbit/s; VBR uses Mutagen's per-file bitrate estimate, rather than
  the lowest individual frame. Unavailable bitrate displays `MP3 (unknown
  bitrate)` and ranks below known quality; a metadata read error displays
  `Unknown`. This is a format/bitrate summary, not an audio-integrity guarantee.
- Folder size sums the logical sizes of all regular files recursively under
  that album directory, including artwork and other sidecars. Symlinks and
  directory overhead are excluded. MB means 1,000,000 bytes, rounded to the
  nearest whole MB (so folders below 0.5 MB display 0). If a nested folder is
  also a separate album, its bytes count in both its own folder size and the
  parent's recursive size. Sizes are collected during discovery on every scan,
  even when audio metadata is cached.
- Track count includes all detected MP3/FLAC files in the configured extension
  scope, including files whose metadata could not be read. An empty directory
  with no detected tracks is not an album.
- Number of issues counts all emitted validation issues for that album, across
  all tracks and severities, including album-level issues. It is blank when none
  are present and updates when rules change, even when audio metadata is cached.
- Parent folder is only the name of the directory one level above the album
  directory, after disc folders have been collapsed (e.g. `Artist` for
  `/music/Artist/Album/CD1`). In HTML, folder size headers and values are
  right-aligned; track count and issue count headers and values are centered.

`catalog.csv` uses nine columns: `no`, `album_artist`, `year`, `album`, `quality`,
`folder_size_mb`, `total_tracks`, `number_of_issues`, `parent_folder`.
`catalog.json` contains
`generated_at` and an `albums` array with the same fields (integer row number, size and counts;
`number_of_issues` is an empty string when none), plus `album_dir` for
unambiguous directory identity. The HTML exposes the directory in each row's
hover tooltip. All catalog formats share the issue report's generation time.

See `docs/ARCHITECTURE.md`, `docs/RULES.md`, `docs/ROADMAP.md`, and `AGENTS.md`.

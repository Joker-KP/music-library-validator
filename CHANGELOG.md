# Changelog

## Unreleased

- Allow folder-specific `rules.yaml` patches to override `filename.regex`,
  including null to disable the pattern check. Overrides inherit down the
  directory tree, support disc folders and revalidate cached tracks without
  rereading audio. Other filename settings remain global.

- Fix silently skipped MP3 artwork minimum-size checks by measuring every ID3
  APIC image header with Pillow, as for FLAC pictures. Undersized covers report
  `small_artwork`; unreadable image headers report `artwork_dimensions_unavailable`
  when a minimum is enabled. Parser version 6 refreshes older metadata caches once
  automatically; no cache deletion or full scan is required. Music stays read-only.

- Add collapsed **Library maintenance** with typed `FRESH START` confirmation.
  Archive the active SQLite cache/sidecars and all report families into unique
  timestamped subfolders, preserve music/configuration/previous archives and lock
  files, reject active scans, roll back ordinary move failures, and clear session
  jobs/results so the next CLI or UI scan can bind to another library.

- Add optional `music-validator serve` and Compose `report-server` (`ui` profile)
  with configured-audit, integrity and audio-identity scan controls, isolated
  worker cancellation/retry, structured phase progress, bounded session job logs,
  and explicit completion-with-findings versus execution failure. Retain CLI
  operation, read-only music/config mounts, existing locks and cache behavior.
- Add overview, album/issue, integrity coverage and audio-match browser views
  with server-side search/sort/pagination, independent report timestamps and
  generation-pinned full exports. No authentication, configuration editing,
  persisted history, report schema changes or additional runtime dependencies.

- Add `speech_marked_in_title` and `instrumental_marked_in_title` to the shipped
  configuration. Missing or blank composer requires a title containing zapowiedź,
  recytacja, wstęp, opowieść or wywiad; missing or blank lyricist requires instr,
  wywiad or zapowiedź. Matching is case-insensitive; failures produce warnings.

- Discover hierarchical `rules.yaml` patches inside the music library. Add/remove
  required tags and custom rules, replace custom definitions by ID, and inherit
  individual artwork/album settings. Track settings follow their actual folders;
  album checks retain the full directory album across discs. Reject `album_rules`
  in collapsed disc directories and keep filename/cross-album/audio settings global.
- Preflight all local patches during counting before writable outputs; reject
  invalid/symlinked/nonregular configurations. Use one configuration snapshot,
  abort and roll back on detected changes, and revalidate cached metadata after
  patch edits/additions/deletions without audio rereads or schema changes.

- Count supported audio files before processing and display per-phase progress
  with file counts, percentage, elapsed time and ETA on stderr. Throttle terminal
  redraws and Docker/cron log lines; add `--no-progress`. Include cache hits and
  explicit file errors, adjust changed count estimates, and mark failed phases.
  Process metadata and optional fingerprints in the same second filesystem walk,
  keeping report schemas and the existing stdout summary unchanged.

- Add opt-in `audio_identity_rules` for copies matched by full-track local
  Chromaprint audio, independent of tags and album directories. Configure `fields`,
  `severity` and `matching.similarity_threshold` (0.95),
  `duration_tolerance_seconds` (2.0) and `minimum_coverage` (0.90). Every pair must
  match to join a group; report `audio_identity_<field>` conflicts on every copy
  in HTML/CSV/JSON and catalog counts. Default metadata validation is unchanged.
- Migrate SQLite to schema 2, retaining all metadata and adding an independent,
  versioned fingerprint cache. Matching/rule edits reuse fingerprints; changed,
  failed or outdated fingerprints and full scans decode again. Report short,
  low-information and failed decoding separately as `audio_fingerprint_error`.
- Include `libchromaprint-tools` in Docker and real MP3/FLAC fingerprint tests in
  CI. Audio decoding remains disabled by default and performs no online lookups
  or writes to music. Document installation, score semantics, limits and tuning.

- **New default validation:** compare `composer`, `lyricist` and `origlyricist`
  for the same title/artist across distinct albums. Emit per-copy, per-field
  `cross_album_*` warnings with conflicting values and reference paths; missing
  versus filled credits is a conflict. Include warnings in all issue reports and
  catalog counts. Configure `cross_album_rules.enabled`, `severity`, `fields`
  and `artist_alias_groups`; aliases apply only to this check. The field list
  defaults to the three credits and accepts other normalized tags; `[]` skips
  comparisons. Field-list changes revalidate cached metadata. Use a temporary SQLite
  projection without persistent schema changes or forced audio rereads.
- Display cross-album details on separate lines for the current value and each
  compared value/reference path. Preserve line breaks in HTML and the same
  multiline message in CSV/JSON.
- Read MP3 ID3 `TCOM` as `composer`, matching FLAC `COMPOSER`, so composer
  presence conditions and comment templates work for both formats. Parser version
  5 refreshes older cached metadata once; later YAML edits reuse the cache.
- Append `Parent folder` (`parent_folder`) to all catalog formats, showing the
  name of the directory one level above the album. Right-align folder size and
  center track count and issue count headers and values in the HTML catalog.
- Add a first `No` column and last `Number of issues` column to the catalog,
  numbering albums from 1 and leaving zero issue counts blank. Round folder
  sizes to whole MB. Apply the same fields to HTML, CSV and JSON exports
  (`no`, `number_of_issues`, integer `folder_size_mb`). Issue-report schemas stay
  unchanged; no metadata reread or database migration is required.
- Add `catalog.html`, `catalog.csv` and `catalog.json` for every detected album,
  including clean albums: album artist, year, album, worst-track quality,
  recursive folder size in decimal MB and track count. Publish catalogs and
  issue reports together in one atomic six-file generation. Count sidecars
  without following symlinks and refresh sizes on metadata-cache hits.
- Cache MP3 stream bitrate under parser version 4, triggering one automatic
  metadata refresh of older entries without changing SQLite schema or audio.
- Fix false `missing_year` issues for MP3 `TDRC`/`TYER` and FLAC `DATE`: derive
  normalized `year` while preserving the complete date. Prefer an explicit
  FLAC `YEAR`, also using it as a missing-date fallback. Apply the same
  derivation to existing cached metadata without audio rereads or schema changes.
- Add custom-rule `when` selectors for format, compilation, field presence and
  literal substring matching; combined selectors use AND logic.
- Add `template.operator` and `template.pattern` (`equals`, `regex` fullmatch,
  `contains` rendered substring). **Breaking configuration change:** remove
  `equals_template` and reject the old `equal` spelling; use a structured
  template with `operator: equals`. Examples list operator before pattern.
  Validate the declarative schema and regex syntax before writes; interpolate
  metadata as literal text in regexes. Add a lyricist/comment-credit example.
- Normalize MP3 `TEXT`/`TOLY`/`TCMP` and FLAC original-lyricist aliases; parser
  version 3 refreshes old metadata once. Later YAML-only rule changes reuse cache.

- Fix false FLAC `small_artwork` issues when PICTURE width/height fields are zero
  or stale: read actual embedded image-header dimensions using Pillow. Keep
  real undersized-cover checks and report unavailable dimensions explicitly.
- Add Pillow as a runtime dependency and bump parser version to 2 so existing
  caches refresh once automatically, without deleting metadata or editing music.

- **Default grouping change:** recognize disc suffixes such as `Some Album Name - CD2`
  and `1999 - Album_CD 02`, with a named disc capture and a required prefix
  separator. Existing explicit YAML patterns still override the new default.
- **New default filename rule:** `filename.allowed_chars: '[]0-9a-zA-Z -()'`
  permits only ASCII letters/digits, spaces, hyphens, square brackets and
  parentheses in the stem. Empty string or null permits all characters. Literal
  Unicode characters can be added; ASCII letter/digit ranges expand. Violations
  produce `filename_allowed_chars` issues. Other filename sub-rules remain independent.
- Both settings re-evaluate cached tracks without rereading audio or migrating SQLite.

- Reject writable outputs inside the music library, output symlinks, unsafe
  SQLite sidecars and multiply linked cache files. Preserve `/music:ro`.
- Separate streaming descriptor-based discovery from CLI orchestration. Read
  audio only through read-only, non-following descriptors and detect changes
  during parsing. Traversal failures roll back without pruning cached tracks.
- Add schema-1 migration, parser-version invalidation, library/mount/extension
  scope binding, scan-generation pruning, summary history and ownership locks.
  Existing metadata is retained during migration and refreshed once afterward.
- Retry cached read failures; regroup and revalidate cache hits under current YAML.
- Support named numeric disc captures and reject ambiguous folder numbering.
  Report disc tag/directory disagreement by default; tags remain authoritative.
- Validate configuration before writable outputs. **Default semantics:** omitted
  options now inherit defaults instead of disabling checks; disable rules
  explicitly. Unknown keys/types/duplicate keys are errors. Templates now permit
  only plain tag identifiers and bounded filename track-number padding.
- Publish HTML/CSV/JSON as immutable generations with one atomic pointer switch;
  migrate existing regular outputs while preserving the public filenames/schema.
- Add real synthetic MP3/FLAC fixtures and integration, failure, migration,
  concurrency, Unicode, escaping and source-immutability regression tests.

## 0.1.0 — baseline scaffold

Initial runnable metadata scanner, SQLite incremental cache, configurable rules, MP3/FLAC support, disc-folder album grouping, static HTML/CSV/JSON reports, Docker packaging and development documentation.

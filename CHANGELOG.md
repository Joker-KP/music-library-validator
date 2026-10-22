# Changelog

## Unreleased

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

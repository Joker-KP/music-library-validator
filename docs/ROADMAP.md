# Development roadmap

## P0 — harden functional baseline

- Completed: count-only discovery followed by one metadata/fingerprint processing
  traversal, per-phase stderr progress with counts/ETA, throttled terminal/log
  output, quiet mode and regression coverage for cache hits, errors and drift.

- Completed: synthetic MP3/FLAC fixtures and subprocess CLI regression tests.
- Completed: strict configuration validation, defaults and restricted template syntax.
- Completed: transactional scan generations, error-aware discovery, scope binding,
  migrations, parser-version invalidation, read retries and ownership locks.
- Completed: output-path checks, descriptor-based read-only traversal, cached
  regrouping, disc extraction/mismatch diagnostics and atomic report generations.
- Completed: disc-marker suffix grouping and configurable filename character
  allowlists, with cached revalidation and Unicode opt-in.
- Completed: FLAC image-header dimensions with Pillow, handling zero/stale PICTURE
  fields and explicit unavailable-dimension diagnostics; parser-version cache refresh.
- Extend image-header measurement to MP3 APIC; full image-integrity checks remain optional future work.
- Confirm permissions, mount behavior, resource use and Unicode behavior on actual
  Synology hardware. Local fixture tests cover Unicode and injected permission errors.
- Added a Linux CI matrix for Python 3.11, 3.12 and 3.14; benchmark large-library scans.

## P1 — quality features

- Completed: hierarchical directory `rules.yaml` patches for required tags,
  custom rules, artwork and album checks, with strict preflight, cached
  revalidation, deterministic inheritance, disc-scope errors and change rollback.

- Completed: dedicated integrity/identity CLI modes overriding YAML enable flags,
  independent report families with coverage/group details, fingerprint-only rechecks,
  schema-4 shared inventory with separate metadata freshness and cross-mode caching.

- Completed: opt-in full-track local Chromaprint matching, configurable similarity,
  duration and coverage, independent schema-2 fingerprint caching, conservative
  complete-link groups and configurable audio-group tag conflicts in all reports.
  Real synthetic MP3/FLAC and cached revalidation are tested; calibrate real-library
  thresholds and benchmark CPU, temporary SQLite disk use and NAS deployment.

- Completed: cross-album composer, lyricist and original-lyricist consistency
  for matching title/artist pairs, configurable artist alias groups and cached
  revalidation, with conflicts included in issue reports and catalog counts.
- Completed: all-album catalog in HTML/CSV/JSON with worst-track quality,
  recursive folder size and track count, published alongside issue reports.
- Completed: normalized `year`/`date` support for required fields and templates,
  including existing cached dates without rereading audio. Calendar validity
  and conflicting year/date checks remain future work.
- Completed: conditional `when` selectors and safe template `equals`, `regex`,
  `contains` comparisons, with MP3/FLAC lyricist/original-lyricist/compilation support.
- Richer Boolean conditions, numeric comparisons and declarative cross-tag
  formulas with safe schema (never eval arbitrary Python/YAML).
- Album sorting, issue filters, issue severity and suppressed/acknowledged flags.
- Completed: opt-in FLAC native stream/MD5 verification and MP3val structure plus
  strict FFmpeg MP3 decoding, schema-3 per-tool caching, incomplete-check retries,
  integrity-only forced rechecks and shared issue/catalog reports. Real synthetic
  damage and missing/mismatched FLAC MD5 are tested; benchmark large NAS libraries.
- External checksum manifests and detection of moved/renamed files.
- Persisted issue history and scan-delta reports (schema migrations and completed
  scan counters are implemented).

## P2 — optional UI

- Completed: confirmed fresh-start maintenance that archives the selected cache
  and all report families into unique timestamped subfolders, preserves previous
  archives and lock inodes, blocks overlapping scans and clears session results.

- Completed: optional local/trusted-LAN report server with no authentication,
  configured/integrity/identity scan controls, isolated worker cancellation,
  structured phase progress, current/last job diagnostics, generation-pinned
  pagination/exports, album filtering and integrity/audio-match views. CLI and
  default one-shot Compose behavior remain available.
- Persisted issue history/deltas and trend charts remain future work;
  configuration editing and job history are outside the current UI scope.
- Keep one-shot static HTML export as the default; never expose music writing functionality implicitly.
- Optional external catalog integration (MusicBrainz/beets) only if clearly requested.

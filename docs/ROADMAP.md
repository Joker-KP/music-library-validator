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
- FLAC stream verification as a separate opt-in operation with cached integrity results.
- Optional checksum verification mode (slower), handling moved/renamed files.
- Persisted issue history and scan-delta reports (schema migrations and completed
  scan counters are implemented).

## P2 — optional UI

- Lightweight authenticated or local-only web interface, advanced filtering, drill-down, trend charts.
- Keep one-shot static HTML export as the default; never expose music writing functionality implicitly.
- Optional external catalog integration (MusicBrainz/beets) only if clearly requested.

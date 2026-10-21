# Development roadmap

## P0 — harden functional baseline

- Add small MP3/FLAC fixture files and end-to-end CLI tests.
- Validate configuration with precise errors and document precedence and normalization rules.
- Fix interrupted-scan semantics using scan generations: delete stale records only after successful complete discovery.
- Add MP3 cover dimensions with Pillow; handle artwork formats and invalid images.
- Confirm disk permission, resource use, Unicode filename behavior, non-ASCII tags on Synology.
- Add explicit disc folder pattern tests (nested, case-insensitive, unmatched) and disc tag-vs-directory mismatch checks.
- Restrict unexpected traversal, prevent symlink escapes and concurrent runs; integrate CI.

## P1 — quality features

- Conditionals and declarative cross-tag formulas with safe schema (never eval arbitrary Python/YAML).
- Album sorting, issue filters, issue severity and suppressed/acknowledged flags.
- FLAC stream verification as a separate opt-in operation with cached integrity results.
- Optional checksum verification mode (slower), handling moved/renamed files.
- SQLite migrations and scan history/delta report.

## P2 — optional UI

- Lightweight authenticated or local-only web interface, advanced filtering, drill-down, trend charts.
- Keep one-shot static HTML export as the default; never expose music writing functionality implicitly.
- Optional external catalog integration (MusicBrainz/beets) only if clearly requested.

# Changelog

## Unreleased — scanner and cache hardening

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

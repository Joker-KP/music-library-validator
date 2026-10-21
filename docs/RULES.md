# Rules reference

See `config/rules.yaml` for runnable configuration.

`library_root` is required and resolves relative to the process working directory.
Omitted options inherit defaults from `config.py`; supplied nested mappings
override individual keys. Unknown keys, duplicate YAML keys, null sections and
wrong types are errors before any database/report write. Booleans must be YAML
booleans, dimension minima must be nonnegative integers or null, and extensions
are a nonempty subset of `.mp3` and `.flac` (case-insensitive).

`disc_folder_pattern` is case-insensitive and full-matched. Existing patterns with
exactly one digit sequence per matched folder continue to work. Use a named
numeric `disc` capture when names contain totals or other numbers:
`'^CD (?P<disc>\d+) of \d+$'`. Disc numbers must be positive. A matched folder
without an unambiguous numeric disc produces an error and rollback, rather than
guessing a disc number. Grouping changes apply to cached tracks without audio reads.

- `required_tags`: normalized names requiring a nonempty string. Defaults title, artist, album, tracknumber, comment.
- `artwork.required`: embedded image required. `min_width` and `min_height` are enforced only for image formats with available decoded dimensions (currently FLAC Picture).
- `filename.regex`: full-match against filename **without extension**. Example `'^\d{2} - .+$'`.
- `filename.match_template`: optional exact string equality using normalized tags. Placeholders must be lowercase identifiers such as `{artist}` or `{title}`. Only filename `tracknumber` allows integer padding, e.g. `{tracknumber:02d}` (width 1–12). Attribute/index access, conversions and nested format specifications are rejected during configuration loading. Unknown tag fields yield explicit evaluation issues. Literal braces can be escaped as `{{` / `}}`.
- `custom_rules`: list of `{id, field, equals_template, severity}` checks. Template fields must exist; comparisons are literal and case-sensitive.
- `album_rules.consistent_album`: require one nonempty album tag value across album's tracks.
- `album_rules.consistent_albumartist`: optional same check for albumartist.
- `album_rules.unique_disc_track`: repeats of the same `(discnumber, tracknumber)` are flagged. Track numbers can be `1` or `1/10`.
- `album_rules.consistent_artwork`: optional check for multiple distinct embedded image hashes across album. This baseline does not require images to be identical in all contexts.
- `album_rules.disc_number_mismatch`: enabled by default; report disagreement between a parsed disc tag and the nearest disc folder. The tag still controls duplicate detection. Set false to disable this diagnostic.

Required-tag and artwork presence checks are enabled by default even if omitted
from YAML. Disable explicitly with `required_tags: []` and
`artwork: {required: false}`. Default album checks are consistent album,
unique disc/track and disc mismatch; albumartist/artwork consistency are opt-in.
`filename.regex` defaults to `'^\d{2} - .+$'`; set null to disable. Custom rules
default to an empty list; each requires a unique identifier, field and
`equals_template`, with severity `info`, `warning` (default) or `error`.
Custom templates allow plain named tag fields only. Regexes/templates are limited
to 4096 characters. Tag comparisons remain literal and case-sensitive.

## Example: custom validation

```yaml
custom_rules:
  - id: comment_format
    field: comment
    equals_template: '{artist} - {album}'
    severity: warning
```

## Intended future rule syntax

- Conditional rules (`when: {format: flac}`, `when: {is_compilation: true}`)
- Filename and parent folder template rules with sanitization/Unicode normalization
- Track title formatting (regex, case, punctuation), disc numbering consistency, numeric track range and gaps, total-tracks sanity, year/date requirements
- Audio stream integrity via external `flac -t`; optional MP3 decoding verification
- Album-level reports of cover resolution, art hash conflicts, and tag value distributions
- Multi-value tag policy, comment selection, custom user fields and richer cross-tag expressions

Never silently modify music files as a repair mechanism.

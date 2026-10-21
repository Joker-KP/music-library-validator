# Rules reference — baseline

See `config/rules.yaml` for runnable configuration.

- `required_tags`: normalized names requiring a nonempty string. Defaults title, artist, album, tracknumber, comment.
- `artwork.required`: embedded image required. `min_width` and `min_height` are enforced only for image formats with available decoded dimensions (currently FLAC Picture).
- `filename.regex`: full-match against filename **without extension**. Example `'^\d{2} - .+$'`.
- `filename.match_template`: optional exact string equality with Python `str.format_map` using normalized tags. `tracknumber` is converted to integer for formatting (`{tracknumber:02d}`); unknown/malformed fields yield issues.
- `custom_rules`: list of `{id, field, equals_template, severity}` checks. Template fields must exist; comparisons are literal and case-sensitive.
- `album_rules.consistent_album`: require one nonempty album tag value across album's tracks.
- `album_rules.consistent_albumartist`: optional same check for albumartist.
- `album_rules.unique_disc_track`: repeats of the same `(discnumber, tracknumber)` are flagged. Track numbers can be `1` or `1/10`.
- `album_rules.consistent_artwork`: optional check for multiple distinct embedded image hashes across album. This baseline does not require images to be identical in all contexts.

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

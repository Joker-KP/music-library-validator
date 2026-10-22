# Rules reference

See `config/rules.yaml` for runnable configuration.

All scans also generate `catalog.html`, `catalog.csv` and `catalog.json` for every
detected album, regardless of validation issues or enabled rules. Catalog fields
and quality/size aggregation are documented in README's Album catalog section.

`library_root` is required and resolves relative to the process working directory.
Omitted options inherit defaults from `config.py`; supplied nested mappings
override individual keys. Unknown keys, duplicate YAML keys, null sections and
wrong types are errors before any database/report write. Booleans must be YAML
booleans, dimension minima must be nonnegative integers or null, and extensions
are a nonempty subset of `.mp3` and `.flac` (case-insensitive).

`disc_folder_pattern` is case-insensitive and full-matched. Its default is
`'^(?:.*[ _.-])?(?:cd|disc|disk)[ _.-]*0*(?P<disc>[1-9][0-9]*)$'`.
It recognizes exact names (`CD1`, `CD 02`) and suffixes (`Some Album Name - CD2`,
`1999 - Album_CD 02`). Prefixes must be separated by a space, underscore, dot or
hyphen; `AlbumCD2` and names with text after the disc number are not matched.
The named capture ignores numbers in the album-name prefix. Existing custom patterns with
exactly one digit sequence per matched folder continue to work. Use a named
numeric `disc` capture when names contain totals or other numbers:
`'^CD (?P<disc>\d+) of \d+$'`. Disc numbers must be positive. A matched folder
without an unambiguous numeric disc produces an error and rollback, rather than
guessing a disc number. Grouping changes apply to cached tracks without audio reads.

- `required_tags`: normalized names requiring a nonempty string. Defaults title, artist, album, tracknumber, comment.
- `artwork.required`: embedded image required. `min_width` and `min_height` compare positive embedded image-header dimensions (currently FLAC Picture). FLAC measurements use Pillow to read the embedded image bytes, ignoring zero or stale declared PICTURE dimensions. A valid 600×600 image passes minima of 500 even when its PICTURE block says 0×0. Unreadable/unsupported image headers or image-size safety-limit failures produce `artwork_dimensions_unavailable` when either minimum is enabled; they are not treated as zero-sized covers. A minimum of 0 or null disables that dimension check. This does not fully decode/verify artwork pixels or audio streams. MP3 APIC dimension measurement remains deferred.
- `filename.regex`: full-match against filename **without extension**. Example `'^\d{2} - .+$'`.
- `filename.allowed_chars`: allowed characters in the filename **without extension**. Default `'[]0-9a-zA-Z -()'`; `''` or null disables this check. ASCII digit ranges (`0-9`, `2-6`) and same-case ASCII letter ranges (`a-z`, `A-Z`, `a-f`) expand inclusively. All other characters, including square brackets, parentheses, spaces, hyphens outside such ranges, backslashes and regex metacharacters, are literal; no surrounding regex brackets or escapes are needed. Descending ranges such as `z-a` are errors. Unicode characters can be added literally. Values must be strings of at most 4096 characters or null. A whitespace-only value permits only those whitespace characters. Each failing track receives one `filename_allowed_chars` issue identifying the unique disallowed characters and their Unicode code points. This sub-rule operates independently of `filename.regex` and `filename.match_template`.
- `filename.match_template`: optional exact string equality using normalized tags. Placeholders must be lowercase identifiers such as `{artist}` or `{title}`. Only filename `tracknumber` allows integer padding, e.g. `{tracknumber:02d}` (width 1–12). Attribute/index access, conversions and nested format specifications are rejected during configuration loading. Unknown tag fields yield explicit evaluation issues. Literal braces can be escaped as `{{` / `}}`.
- `custom_rules`: list of `{id, field, template, when, severity}` checks. `id`, target `field`, `template.operator` and `template.pattern` are required. Optional `when` filters tracks before rendering; optional `severity` is `info`, `warning` (default) or `error`.
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
`template`, with severity `info`, `warning` (default)
or `error`. Custom templates allow plain named tag fields only. Configured
regexes/templates are limited to 4096 characters. Rendered custom regexes are also
limited to 4096 characters. Comparisons are case-sensitive unless the configured
regex explicitly supplies flags such as `(?i)`.

The allowed-character default is deliberately ASCII. For example, allow Polish
letters and underscores by setting:

```yaml
filename:
  allowed_chars: '[]0-9a-zA-Z -()ĄĆĘŁŃÓŚŹŻąćęłńóśźż_'
```

Allow all filename characters with `filename: {allowed_chars: ''}`. Omission of
the key enables the default allowlist. YAML-only changes to this setting and the
disc-folder pattern take effect on the next scan without rereading audio.

## Year and date fields

`required_tags: [year]` is supported for MP3 and FLAC. MP3 `TDRC` and legacy
`TYER` normalize to `date`; FLAC `DATE` and `YEAR` normalize to `date` and
`year`. When `year` is missing or blank, derive it from the first four ASCII
digits of the trimmed `date`, followed by the end of the value, whitespace, `-`,
`/`, `.`, or a timestamp `T`/`t` separator. For example, `1999`, `2001-02-03`,
`2001/02/03`, `2001.02.03` and `2001-02-03T12:34:56` expose a year. Preserve
the complete original `date` value, including whitespace. Free text such as
`recorded in 1999`, `unknown` or `1999junk` does not supply a year.

A nonblank explicit `YEAR` takes precedence over `DATE`, including when they
disagree. If `date` is absent or blank, that explicit year also supplies `date`.
Both normalized fields work in required fields, conditions and templates.
`required_tags` checks nonblank presence only; it does not validate calendar
dates or detect conflicting year/date values. Normalization also runs on cached
tags before validation, so existing cached dates need no metadata reread or
`--full-scan`. Audio files and tags are never modified.

## Example: custom validation

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

If lyricist is `Jan Łódzki`, this checks that the comment includes the exact
substring `słowa: Jan Łódzki`. Additional text before/after the credit is allowed.
When lyricist is absent, empty or whitespace-only, this rule is skipped.

## Conditional selectors

Omit `when` (or use `{}`) to apply a rule to all readable tracks. Selectors in
one `when` mapping are combined with AND; each must match. The `field` inside
`when` selects a condition's input, while the rule's top-level `field` selects
the value to validate. No expressions or executable YAML/Python are evaluated.

| Selector | Meaning |
| --- | --- |
| `when: {format: flac}` | Only FLAC tracks; supported values are lowercase `flac` and `mp3`. |
| `when: {is_compilation: true}` | Only tracks whose normalized `compilation` tag is true. |
| `when: {field: origlyricist, is_present: true}` | The field exists and is not empty/whitespace-only. |
| `when: {field: origlyricist, is_present: false}` | The field is absent or blank. A value of `0` is present. |
| `when: {field: title, contains: some-string}` | The nonblank field contains that exact, case-sensitive literal substring; no template/regex expansion occurs. |

`when.field` requires `is_present`, `contains`, or both; those predicates require
`field`. `is_compilation` and `is_present` must be YAML booleans. `contains` must
be a nonempty string of at most 4096 characters. Null/unknown selectors and
unsupported types are rejected before opening writable outputs.

Compilation reads MP3 `TCMP` / FLAC `COMPILATION`, with trimmed, case-insensitive
values `1`, `true`, `yes` meaning true, and `0`, `false`, `no` or absent/blank
meaning false. Unrecognized nonblank values match neither true nor false.
Albumartist text is not used to infer compilation status.

Combined example:

```yaml
when:
  format: flac
  is_compilation: true
  field: title
  contains: '[Live]'
```

## Template operators

`template.pattern` is a string with optional plain tag placeholders, such as
`{artist}`, `{album}`, `{lyricist}` and `{origlyricist}`. Attribute/index access,
conversions and format specifications are forbidden for custom templates. Both
`pattern` and `operator` must be supplied. Empty patterns are valid.

| `template.operator` | Comparison |
| --- | --- |
| `equals` | The field equals the entire rendered pattern literally. |
| `contains` | The field contains the entire rendered pattern as a literal substring. |
| `regex` | The entire field matches the rendered regular expression (`fullmatch`). Use `.*` explicitly when matching surrounding text. |

Literal braces are doubled because patterns are templates: `\d{{2}}` renders
the regex `\d{2}`, and `{{artist}}` means literal `{artist}`. For example:

```yaml
template:
  operator: regex
  pattern: '^\d{{2}} - {title}$'
```

Only the configured pattern supplies regex syntax. Interpolated tag values are
regex-escaped: a lyricist `A (B)` matches those literal parentheses. Regex syntax
is validated during configuration loading with placeholder stand-ins. Missing
template fields, an invalid rendered regex or an oversized rendered regex
produce an explicit error issue under that rule ID. A false condition skips
the rule before rendering, avoiding irrelevant missing-placeholder errors.
Missing target fields compare as an empty string. An empty `equals` or `regex`
pattern therefore matches only an empty field, while empty `contains` always passes.

The legacy `equals_template` key and old `equal` operator are unsupported and
rejected during configuration loading. Migrate equality checks to:

```yaml
template:
  operator: equals
  pattern: '{artist} - {album}'
```

This is a YAML-only migration; metadata cache contents remain reusable.

MP3 `TCOM` maps to `composer`, matching FLAC `COMPOSER`. Use `composer` in
required tags, presence conditions and templates for both formats. Parser version
5 refreshes older cached metadata once automatically; no full scan is needed.

For example, require a composer credit only when the composer is present:

```yaml
custom_rules:
  - id: comment_has_composer
    when: {field: composer, is_present: true}
    field: comment
    template:
      operator: contains
      pattern: 'muzyka: {composer}'
    severity: warning
```

The pattern is literal and case-sensitive: `muzykaa:` and `muzyka:` are different.

MP3 `TEXT`, `TOLY`, `TCMP` map to normalized `lyricist`, `origlyricist`,
`compilation`. FLAC fields use those names; `ORIGINALLYRICIST` and
`ORIGINAL_LYRICIST` also alias to `origlyricist` when the canonical tag is absent.
The existing first-value policy still applies to multi-valued tags. Parser
version 3 refreshes older metadata once to expose the added MP3 tags/FLAC aliases;
subsequent YAML-only rule edits reuse cached metadata.

## Cross-album credit consistency

`cross_album_rules` is a mapping with these optional settings:

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Check matching tracks across distinct album directories. |
| `severity` | `warning` | Severity for these issues: `info`, `warning`, or `error`. |
| `fields` | `[composer, lyricist, origlyricist]` | Normalized tag names to compare; `[]` skips comparisons. |
| `artist_alias_groups` | `[]` | A list of lists of equivalent artist names. |

For example:

```yaml
cross_album_rules:
  fields: [composer, lyricist, origlyricist]
  artist_alias_groups:
    - [Wolna Grupa Bukowina, Wojciech Bellon]
    - [U Pana Boga za Piecem, Sylwek Szweda, Towarzstwo Wzajemnej Adoracji]
```

Each group requires at least two nonblank strings. Duplicate names, including
overlaps between groups after trimming, are configuration errors before output
writes. Unknown options, null sections, nonboolean `enabled` and invalid
severities are rejected. The first name is the group's identity in diagnostics;
all group members match equally, without changing metadata or other validators.

The matching key is the pair of trimmed title and trimmed artist, with aliases
applied to the artist only. Comparisons are case-sensitive and preserve internal
whitespace, punctuation and Unicode text. There is no fuzzy title matching or
inference from album artist, composer, filenames or live-version labels.
Unreadable tracks and missing/blank title or artist are excluded.

When a key occurs in at least two album directories, compare the trimmed values
of every configured `fields` entry independently. The list replaces the default
`composer`, `lyricist` and `origlyricist` selection. Entries must be unique
lowercase field identifiers, as for `required_tags`; nulls, wrong types and invalid
identifiers are configuration errors before writes. Other normalized tags such as
`genre`, `comment` or `album` are allowed. Fields refer to metadata tag values,
not filesystem properties or inferred identities. Selecting `artist` compares
original artist tag values even when aliases made their identities equivalent.
This setting does not add support for reading new ID3 frames. Missing, empty
and whitespace-only values all compare as missing; missing versus filled is a
conflict. The first-value metadata policy still applies. Copies only within one
album, including its disc folders, do not trigger this rule.

Each differing field emits one issue for every matching copy, including copies
with the most common value. Rule IDs follow `cross_album_<field>`, e.g.
`cross_album_composer`, `cross_album_lyricist`, or `cross_album_genre`. Diagnostics include
the key, that copy's value, all distinct values and a representative file path
for each value. The current value has its own line, followed by a separate line
for each compared value/reference path. HTML preserves these line breaks; CSV
and JSON carry the same multiline message. No copy is assumed authoritative. Issues use the existing
album/path/rule/severity/message schema in all reports, and count in the catalog.

Field lists, aliases, severity and enablement re-evaluate cached metadata on every scan.
No schema change, audio read or parser-version bump is required by this feature.

## Audio identity rules

`audio_identity_rules` compares tags among copies matched by local Chromaprint
audio fingerprints. It does not depend on title, artist, aliases, filenames or
album tags for matching. Copies in the same directory album, including CD1/CD2,
are included. Directory-based album and catalog grouping remain unchanged.

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

All settings are optional and inherit these values except `enabled`, whose default
is **false**. Partial `matching` mappings inherit omitted keys individually.
`enabled` must be boolean. `severity` is `info`, `warning`, or `error` and applies
to tag conflicts. `fields` replaces the default list; it accepts unique lowercase
normalized tag identifiers, including `title`, `artist`, `composer`, `lyricist`,
`origlyricist` and `genre`. It cannot add unsupported ID3 frame readers. `[]`
skips backend initialization, decoding and comparisons, even if enabled.

| Matching option | Default | Validation and meaning |
| --- | --- | --- |
| `similarity_threshold` | `0.95` | Finite number in `(0, 1]`; minimum fraction of overlapping fingerprint words differing by at most two of 32 bits. |
| `duration_tolerance_seconds` | `2.0` | Finite nonnegative number; maximum difference in decoded durations, inclusive. |
| `minimum_coverage` | `0.90` | Finite number in `(0, 1]`; minimum overlap frame count divided by the longer fingerprint length, inclusive. |

Booleans, strings, nulls, NaN/infinity, unknown keys and invalid ranges are rejected
before output writes. No threshold is a probability. Fingerprints are aligned at
integer fingerprint-frame offsets, with at least 32 overlapping frames regardless
of configured coverage. Similarity and coverage must both pass at the same offset;
the first qualifying offset in deterministic alignment-vote order is used.

Groups are built in sorted path order. A new member must match every existing
member; among compatible groups, the earliest representative path wins. Groups
are disjoint and are not merged transitively. Consequently ambiguous chain matches
may remain in different groups rather than report a speculative conflict. At
thresholds >=0.95, a SQLite index of partial adjacent fingerprint words retrieves
duration-compatible candidates without using tags. Every fourth adjacent pair is
indexed; the query uses all adjacent pairs. Within two bit errors per word, at
least one of three disjoint word parts is unchanged; nine two-word combinations
cover both words. With >=32 overlapping frames and >=0.95 similarity, at least
one indexed pair survives. Lower thresholds fall back to all duration-compatible
candidates; below 0.75 comparison also enumerates all feasible frame alignments.
Low thresholds and collections of very similar audio can require quadratic work.

For each differing configured field, emit `audio_identity_<field>` for every group
member. Compare trimmed tag strings case-sensitively, with absent/empty/whitespace
equivalent. Each message contains a deterministic local group label, group size,
minimum accepted pair similarity and coverage, current value and one representative
path for every distinct value. Labels hash sorted member paths; renames, membership
or matching changes can change them. They are not universal audio IDs. All issue
formats preserve the same multiline message and include conflicts in catalog counts.

Unusable audio produces `audio_fingerprint_error` at warning severity, independently
of the configured conflict severity and metadata readability. A track must decode
successfully, be at least 10 seconds long and yield at least 32 fingerprint frames
and eight distinct words. These checks exclude silence/nearly constant audio and
short/empty fingerprints conservatively. Metadata-error tracks retain their own
diagnostics and are excluded from tag comparison. Decode failures/low-information
results are retried on subsequent enabled scans. No automatic fixes occur.

Extraction uses fpcalc algorithm 2, raw full-track fingerprints and decoded sample
durations, not its default 120-second excerpt. One chunk of up to 24 hours ensures
unknown/zero duration headers still work; longer inputs are explicitly rejected.
Each decoder process has a 300-second timeout. Incomplete/nonzero-exit output is
rejected. Reading successful audio does not certify stream integrity. The backend
receives only a verified read-only file descriptor and a forced MP3/FLAC demuxer.

The optional dependency is `fpcalc` (`libchromaprint-tools` on Ubuntu), included in
the Docker image. There are no online lookups or API keys. Missing/nonfunctional
fpcalc fails before writable outputs are created. SQLite schema 2 stores raw words,
duration, size/mtime signature, backend/settings version and failure status in
`audio_fingerprints`. It preserves all cached tag metadata and its parser version.
Fingerprints and issue generation share the scan transaction: interruptions roll
back, and publication retains the existing complete-generation behavior.

Field/severity/matching changes re-evaluate cached fingerprints without decoding.
Changed files, absent/failed entries, fpcalc/decoder/extraction-version changes and
`--full-scan` refresh fingerprints. Removed or changed paths prune stale audio cache
entries on all scans, including when the feature is disabled. Like the metadata
cache, external changes preserving size and nanosecond mtime require `--full-scan`.
Renames are not detected as moves. No issue history is added.

## Intended future rule syntax

- Richer Boolean conditions (OR/negation) and numeric comparisons
- Filename and parent folder template rules with sanitization/Unicode normalization
- Track title formatting (regex, case, punctuation), disc numbering consistency, numeric track range and gaps, total-tracks sanity, calendar date validity and year/date consistency
- Audio stream integrity via external `flac -t`; optional MP3 decoding verification
- Album-level reports of cover resolution, art hash conflicts, and tag value distributions
- Multi-value tag policy, comment selection, custom user fields and richer cross-tag expressions

Never silently modify music files as a repair mechanism.

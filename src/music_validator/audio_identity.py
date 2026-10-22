"""Audio-only matching and tag consistency over normalized cached fingerprints."""

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import pairwise

from .validation import issue


def landmarks(fingerprint):
    """Multi-index keys for adjacent 32-bit words, retaining temporal positions.

    Split each word into three parts: within two bit errors at least one part
    is unchanged. The nine combinations across two words include an exact key.
    These keys retrieve candidates; full aligned fingerprints decide matches.
    """
    for position, (first, second) in enumerate(pairwise(fingerprint)):
        for left in range(3):
            a = (first >> (11 * left)) & 2047
            for right in range(3):
                b = (second >> (11 * right)) & 2047
                yield ((left * 3 + right) << 22) | (a << 11) | b, position


@dataclass(frozen=True)
class Match:
    similarity: float
    coverage: float


def compare(a, b, matching):
    """Aligned fraction of words within two bit errors; score is not probability.

    Coverage is overlapping fingerprint frames divided by the longer sequence.
    At thresholds >= .75, an accepted alignment necessarily has an adjacent pair
    of matching words (fingerprints have >=32 frames), so landmarks find it.
    Lower thresholds deliberately use exhaustive offsets and duration candidates.
    """
    if abs(a["duration"] - b["duration"]) > matching["duration_tolerance_seconds"]:
        return None
    left, right = a["fingerprint"], b["fingerprint"]
    if not left or not right:
        return None
    longest = max(len(left), len(right))
    required = max(32, math.ceil(matching["minimum_coverage"] * longest))
    if min(len(left), len(right)) < required:
        return None
    if left == right:
        return Match(1.0, 1.0)
    if matching["similarity_threshold"] < 0.75:
        offsets = range(required - len(right), len(left) - required + 1)
    else:
        positions = defaultdict(list)
        for token, position in landmarks(left):
            positions[token].append(position)
        votes = Counter()
        for token, position in landmarks(right):
            for other in positions.get(token, ()):
                offset = other - position
                if required - len(right) <= offset <= len(left) - required:
                    votes[offset] += 1
        offsets = sorted(votes, key=lambda offset: (-votes[offset], abs(offset), offset))
    for offset in offsets:
        start_left, start_right = max(0, offset), max(0, -offset)
        overlap = min(len(left) - start_left, len(right) - start_right)
        if overlap < required:
            continue
        hits = sum(
            (left[start_left + i] ^ right[start_right + i]).bit_count() <= 2 for i in range(overlap)
        )
        similarity = hits / overlap
        if similarity >= matching["similarity_threshold"]:
            return Match(similarity, overlap / longest)
    return None


def validate_audio_identity(index, options, progress=None, on_group=None):
    """Deterministic greedy complete-link groups; no transitive chain merging.

    The index stores fingerprints/tag projections in SQLite. Python retains path
    memberships, and loads only the tracks needed for a comparison/group report.
    """
    if not options["fields"]:
        return
    groups, memberships, evidence = {}, {}, {}
    for row in index.rows():
        candidate_groups = sorted(
            {memberships[path] for path in index.candidate_paths(row, options["matching"])}
        )
        for group in candidate_groups:
            matches = [compare(index.get(path), row, options["matching"]) for path in groups[group]]
            if all(match is not None for match in matches):
                groups[group].append(row["path"])
                memberships[row["path"]] = group
                previous = evidence[group]
                evidence[group] = Match(
                    min(previous.similarity, *(match.similarity for match in matches)),
                    min(previous.coverage, *(match.coverage for match in matches)),
                )
                break
        else:
            groups[row["path"]] = [row["path"]]
            memberships[row["path"]] = row["path"]
            evidence[row["path"]] = Match(1.0, 1.0)
        if progress is not None:
            progress(1)
    for group, paths in groups.items():
        if len(paths) < 2:
            continue
        rows = [index.get(path) for path in paths]
        identifier = hashlib.sha256(json.dumps(paths, ensure_ascii=False).encode()).hexdigest()[:16]
        confidence = evidence[group]
        if on_group is not None:
            on_group(
                {
                    "id": identifier,
                    "similarity_min": confidence.similarity,
                    "coverage_min": confidence.coverage,
                    "conflicting_fields": [
                        field
                        for field in options["fields"]
                        if len({row["values"][field] for row in rows}) > 1
                    ],
                    "tracks": [
                        {"path": row["path"], "album": row["album_dir"], "values": row["values"]}
                        for row in rows
                    ],
                }
            )
        for field in options["fields"]:
            references = {}
            for row in rows:
                references.setdefault(row["values"][field], row["path"])
            if len(references) < 2:
                continue
            details = "\n".join(
                f"- {value!r} at {references[value]}"
                if value
                else f"- <missing> at {references[value]}"
                for value in sorted(references)
            )
            for row in rows:
                actual = repr(row["values"][field]) if row["values"][field] else "<missing>"
                yield issue(
                    row,
                    "audio_identity_" + field,
                    f"Audio group {identifier}: conflicting {field} ({len(paths)} copies; "
                    f"similarity >= {confidence.similarity:.4f}; coverage >= {confidence.coverage:.4f})\n"
                    f"This track: {actual}\nValues in audio group:\n{details}",
                    options["severity"],
                )

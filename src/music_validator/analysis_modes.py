"""Execution scopes and coverage summaries for dedicated audio analyses."""

from collections import Counter

from . import database
from .integrity import selected_checks


def select_mode(config, integrity_only, identity_only, recheck_integrity, recheck_identity):
    if integrity_only and identity_only:
        raise ValueError("--audio-integrity-only and --audio-identity-only are mutually exclusive")
    mode = "integrity" if integrity_only else "identity" if identity_only else "configured"
    integrity = config["audio_integrity"]
    identity = config["audio_identity_rules"]
    if mode == "integrity":
        integrity["enabled"], identity["enabled"] = True, False
        if not any(selected_checks(integrity, ext) for ext in config["extensions"]):
            raise ValueError(
                "--audio-integrity-only requires at least one applicable integrity check"
            )
    elif mode == "identity":
        identity["enabled"], integrity["enabled"] = True, False
        if not identity["fields"]:
            raise ValueError(
                "--audio-identity-only requires a nonempty audio_identity_rules.fields list"
            )
    if recheck_integrity and not integrity["enabled"]:
        raise ValueError(
            "--recheck-integrity requires audio_integrity.enabled: true or --audio-integrity-only"
        )
    if recheck_identity and not (identity["enabled"] and identity["fields"]):
        raise ValueError(
            "--recheck-identity requires enabled audio identity with fields or --audio-identity-only"
        )
    return mode


def report_analysis(conn, mode, result, config):
    statistics = {"files_considered": result.seen}
    if mode == "integrity":
        statuses, sources = Counter(), Counter()
        for row in database.iter_integrity_activity(conn):
            statuses[row["status"]] += 1
            sources[row["source"]] += 1
        statistics.update(
            checks_executed=sources["executed"],
            checks_reused=sources["cached"],
            files_without_applicable_check=statuses["not_checked"],
            checks_by_status={
                status: statuses[status] for status in ("passed", "warning", "failed", "incomplete")
            },
        )
        options = config["audio_integrity"]
    else:
        statuses, sources = Counter(), Counter()
        for row in database.iter_identity_activity(conn):
            statuses[row["status"]] += 1
            sources[row["source"]] += 1
        groups = conflicts = 0
        for group in database.iter_identity_groups(conn):
            groups += 1
            conflicts += bool(group["conflicting_fields"])
        statistics.update(
            metadata_read=result.read,
            fingerprint_attempts=result.audio_read,
            fingerprints_extracted=result.audio_read - result.audio_errors,
            fingerprints_reused=sources["cached"],
            fingerprint_errors=result.audio_errors,
            matched_groups=groups,
            groups_with_conflicts=conflicts,
            tracks_by_status={
                status: statuses[status]
                for status in (
                    "matched",
                    "unmatched",
                    "fingerprint_error",
                    "metadata_error",
                    "skipped_integrity",
                )
            },
        )
        options = config["audio_identity_rules"]
    return {"mode": mode, "settings": options, "statistics": statistics}

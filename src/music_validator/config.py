"""Validate the declarative configuration before opening any writable outputs."""

import math
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from string import Formatter

import yaml

from .characters import DEFAULT_ALLOWED_CHARS, allowed_characters
from .cross_album import DEFAULT_FIELDS
from .custom_rules import OPERATORS
from .grouping import DEFAULT_DISC_PATTERN

DEFAULTS = {
    "extensions": [".mp3", ".flac"],
    "disc_folder_pattern": DEFAULT_DISC_PATTERN,
    "required_tags": ["title", "artist", "album", "tracknumber", "comment"],
    "artwork": {"required": True, "min_width": 500, "min_height": 500},
    "filename": {
        "regex": r"^\d{2} - .+$",
        "match_template": None,
        "allowed_chars": DEFAULT_ALLOWED_CHARS,
    },
    "custom_rules": [],
    "audio_integrity": {
        "enabled": False,
        "flac": {"native_test": True},
        "mp3": {"structure_check": True, "decode_check": True},
        "timeout_seconds": 300,
    },
    "audio_identity_rules": {
        "enabled": False,
        "fields": ["title", "artist", "composer"],
        "severity": "warning",
        "matching": {
            "similarity_threshold": 0.95,
            "duration_tolerance_seconds": 2.0,
            "minimum_coverage": 0.90,
        },
    },
    "cross_album_rules": {
        "enabled": True,
        "severity": "warning",
        "fields": list(DEFAULT_FIELDS),
        "artist_alias_groups": [],
    },
    "album_rules": {
        "consistent_album": True,
        "consistent_albumartist": False,
        "unique_disc_track": True,
        "consistent_artwork": False,
        "disc_number_mismatch": True,
    },
}
NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


class ConfigLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ValueError("Configuration keys must be strings")  # noqa: TRY004
        if key in result:
            raise ValueError(f"Duplicate configuration key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


ConfigLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _object(value, allowed, where):
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")  # noqa: TRY004
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"Unknown {where} options: {sorted(unknown)}")


def _name(value, where):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(f"{where} must be a lowercase field identifier")


def _regex(value, where, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"{where} must be a nonempty regex of at most 4096 characters")
    try:
        re.compile(value, re.IGNORECASE if where == "disc_folder_pattern" else 0)
    except re.error as exc:
        raise ValueError(f"Invalid {where}: {exc}") from exc


def check_template(value, where, filename=False):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError(f"{where} template must be a string of at most 4096 characters")
    try:
        for _, field, spec, conversion in Formatter().parse(value):
            if field is None:
                continue
            if not NAME.fullmatch(field) or conversion:
                raise ValueError("only named tag fields are allowed")
            if spec and not (
                filename and field == "tracknumber" and re.fullmatch(r"0?(?:[1-9]|1[0-2])d", spec)
            ):
                raise ValueError("only tracknumber integer padding up to width 12 is allowed")
    except ValueError as exc:
        raise ValueError(f"Invalid {where} template: {exc}") from exc


def check_when(condition):
    _object(
        condition,
        {"format", "is_compilation", "field", "is_present", "contains"},
        "custom_rules.when",
    )
    if "format" in condition and condition["format"] not in ("mp3", "flac"):
        raise ValueError("custom_rules.when.format must be mp3 or flac")
    for key in ("is_compilation", "is_present"):
        if key in condition and type(condition[key]) is not bool:
            raise ValueError(f"custom_rules.when.{key} must be a boolean")
    if "field" in condition:
        _name(condition["field"], "custom_rules.when.field")
        if not ({"is_present", "contains"} & condition.keys()):
            raise ValueError("custom_rules.when.field requires is_present or contains")
    elif {"is_present", "contains"} & condition.keys():
        raise ValueError("custom_rules.when.is_present/contains requires field")
    if "contains" in condition and (
        not isinstance(condition["contains"], str)
        or not condition["contains"]
        or len(condition["contains"]) > 4096
    ):
        raise ValueError(
            "custom_rules.when.contains must be a nonempty string of at most 4096 characters"
        )


def check_custom_template(rule):
    template = rule["template"]
    _object(template, {"pattern", "operator"}, "custom_rules.template")
    if "pattern" not in template or "operator" not in template:
        raise ValueError("custom_rules.template requires operator and pattern")
    if template["operator"] not in OPERATORS:
        raise ValueError("custom_rules.template.operator must be equals, regex or contains")
    check_template(template["pattern"], "custom_rules.template.pattern")
    if template["operator"] == "regex":
        # Validate syntax with literal stand-ins for tag values, before outputs.
        probe = template["pattern"].format_map(defaultdict(lambda: "tag"))
        try:
            re.compile(probe)
        except re.error as exc:
            raise ValueError(f"Invalid custom_rules.template.pattern regex: {exc}") from exc


def check_cross_album_rules(options):
    if type(options["enabled"]) is not bool:
        raise ValueError("cross_album_rules.enabled must be a boolean")
    if options["severity"] not in ("info", "warning", "error"):
        raise ValueError("cross_album_rules.severity must be info, warning or error")
    fields = options["fields"]
    if not isinstance(fields, list):
        raise ValueError("cross_album_rules.fields must be a list")  # noqa: TRY004
    for field in fields:
        _name(field, "cross_album_rules.fields entry")
    if len(fields) != len(set(fields)):
        raise ValueError("cross_album_rules.fields must not contain duplicate names")
    groups = options["artist_alias_groups"]
    if not isinstance(groups, list):
        raise ValueError("cross_album_rules.artist_alias_groups must be a list")  # noqa: TRY004
    seen = set()
    for group in groups:
        if not isinstance(group, list) or len(group) < 2:
            raise ValueError("Each artist alias group must be a list of at least two names")
        for name in group:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Artist alias names must be nonblank strings")
            name = name.strip()
            if name in seen:
                raise ValueError(f"Duplicate or overlapping artist alias name: {name!r}")
            seen.add(name)


def check_audio_identity_rules(options):
    if type(options["enabled"]) is not bool:
        raise ValueError("audio_identity_rules.enabled must be a boolean")
    if options["severity"] not in ("info", "warning", "error"):
        raise ValueError("audio_identity_rules.severity must be info, warning or error")
    fields = options["fields"]
    if not isinstance(fields, list):
        raise ValueError("audio_identity_rules.fields must be a list")  # noqa: TRY004
    for field in fields:
        _name(field, "audio_identity_rules.fields entry")
    if len(fields) != len(set(fields)):
        raise ValueError("audio_identity_rules.fields must not contain duplicate names")
    for name, value in options["matching"].items():
        try:
            finite = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"audio_identity_rules.matching.{name} must be a finite number")
        if name == "duration_tolerance_seconds":
            valid = value >= 0
        else:
            valid = 0 < value <= 1
        if not valid:
            bounds = "nonnegative" if name == "duration_tolerance_seconds" else "in (0, 1]"
            raise ValueError(f"audio_identity_rules.matching.{name} must be {bounds}")


def check_audio_integrity(options):
    switches = [("enabled", options["enabled"])]
    switches.extend(
        (f"{section}.{name}", value)
        for section in ("flac", "mp3")
        for name, value in options[section].items()
    )
    for name, value in switches:
        if type(value) is not bool:
            raise ValueError(f"audio_integrity.{name} must be a boolean")
    timeout = options["timeout_seconds"]
    try:
        valid = type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("audio_integrity.timeout_seconds must be a finite positive number")


def load_config(path: Path) -> dict:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=ConfigLoader)
    return validate_config(data)


def validate_config(data) -> dict:
    """Build and validate a configuration, also reusable for inherited patches."""
    _object(data, set(DEFAULTS) | {"library_root"}, "configuration")
    if not isinstance(data.get("library_root"), str) or not data["library_root"].strip():
        raise ValueError("library_root must be a nonempty path string")
    config = deepcopy(DEFAULTS)
    for key, value in data.items():
        if key in (
            "artwork",
            "filename",
            "album_rules",
            "cross_album_rules",
            "audio_identity_rules",
            "audio_integrity",
        ):
            _object(value, set(DEFAULTS[key]), key)
            for option, setting in value.items():
                if (key == "audio_identity_rules" and option == "matching") or (
                    key == "audio_integrity" and option in ("flac", "mp3")
                ):
                    _object(setting, set(DEFAULTS[key][option]), f"{key}.{option}")
                    config[key][option].update(setting)
                else:
                    config[key][option] = setting
        else:
            config[key] = value
    extensions = config["extensions"]
    if (
        not isinstance(extensions, list)
        or not extensions
        or any(not isinstance(x, str) or x.lower() not in (".mp3", ".flac") for x in extensions)
    ):
        raise ValueError("extensions must be a nonempty list containing only .mp3 and/or .flac")
    config["extensions"] = sorted({x.lower() for x in extensions})
    _regex(config["disc_folder_pattern"], "disc_folder_pattern")
    if not isinstance(config["required_tags"], list):
        raise ValueError("required_tags must be a list")  # noqa: TRY004
    for field in config["required_tags"]:
        _name(field, "required_tags entry")
    art = config["artwork"]
    if type(art["required"]) is not bool:
        raise ValueError("artwork.required must be a boolean")
    for key in ("min_width", "min_height"):
        if art[key] is not None and (type(art[key]) is not int or art[key] < 0):
            raise ValueError(f"artwork.{key} must be a nonnegative integer or null")
    for key, value in config["album_rules"].items():
        if type(value) is not bool:
            raise ValueError(f"album_rules.{key} must be a boolean")
    check_cross_album_rules(config["cross_album_rules"])
    check_audio_identity_rules(config["audio_identity_rules"])
    check_audio_integrity(config["audio_integrity"])
    fc = config["filename"]
    _regex(fc["regex"], "filename.regex", nullable=True)
    allowed_characters(fc["allowed_chars"])
    if fc["match_template"] is not None:
        check_template(fc["match_template"], "filename.match_template", filename=True)
    if not isinstance(config["custom_rules"], list):
        raise ValueError("custom_rules must be a list")  # noqa: TRY004
    identifiers = set()
    for rule in config["custom_rules"]:
        _object(
            rule,
            {"id", "field", "template", "when", "severity"},
            "custom_rules entry",
        )
        for key in ("id", "field", "template"):
            if key not in rule:
                raise ValueError(f"custom_rules entry requires {key}")
        _name(rule["id"], "custom rule id")
        _name(rule["field"], "custom rule field")
        if rule["id"] in identifiers:
            raise ValueError(f"Duplicate custom rule id: {rule['id']}")
        identifiers.add(rule["id"])
        if rule.get("severity", "warning") not in ("info", "warning", "error"):
            raise ValueError("custom rule severity must be info, warning or error")
        if "when" in rule:
            check_when(rule["when"])
        check_custom_template(rule)
    return config

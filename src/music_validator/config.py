"""Validate the declarative configuration before opening any writable outputs."""

import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from string import Formatter

import yaml

from .characters import DEFAULT_ALLOWED_CHARS, allowed_characters
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


def load_config(path: Path) -> dict:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=ConfigLoader)
    _object(data, set(DEFAULTS) | {"library_root"}, "configuration")
    if not isinstance(data.get("library_root"), str) or not data["library_root"].strip():
        raise ValueError("library_root must be a nonempty path string")
    config = deepcopy(DEFAULTS)
    for key, value in data.items():
        if key in ("artwork", "filename", "album_rules"):
            _object(value, set(DEFAULTS[key]), key)
            config[key].update(value)
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

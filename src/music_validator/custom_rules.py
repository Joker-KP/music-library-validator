"""Declarative predicates and comparisons over normalized track metadata."""

import re

OPERATORS = ("equals", "regex", "contains")


class _RegexTags:
    def __init__(self, tags):
        self.tags = tags

    def __getitem__(self, key):
        value = self.tags[key]
        if len(value) > 4096:
            raise ValueError("Interpolated custom regex value exceeds 4096 characters")
        return re.escape(value)


def _compilation(tags):
    value = str(tags.get("compilation", "")).strip().casefold()
    if value in ("1", "true", "yes"):
        return True
    if value in ("", "0", "false", "no"):
        return False
    return None


def matches_when(metadata, condition):
    """All specified selectors must match; absent conditions apply to all tracks."""
    tags = metadata.get("tags", {})
    if "format" in condition and metadata.get("format") != condition["format"]:
        return False
    if "is_compilation" in condition and _compilation(tags) is not condition["is_compilation"]:
        return False
    if "field" in condition:
        value = tags.get(condition["field"])
        present = value is not None and bool(str(value).strip())
        if "is_present" in condition and present != condition["is_present"]:
            return False
        if "contains" in condition and (not present or condition["contains"] not in str(value)):
            return False
    return True


def failure_message(rule, metadata):
    """Return a failed comparison's diagnostic, or None for a pass/skipped rule."""
    if not matches_when(metadata, rule.get("when", {})):
        return None
    tags = metadata.get("tags", {})
    actual = str(tags.get(rule["field"], ""))
    operator = rule["template"]["operator"]
    pattern = rule["template"]["pattern"]
    if operator == "regex":
        # Tag values are data, never regex instructions. Only the configured
        # pattern supplies regex syntax; template placeholders supply literals.
        expected = pattern.format_map(_RegexTags(tags))
        if len(expected) > 4096:
            raise ValueError("Rendered custom regex exceeds 4096 characters")
        if re.fullmatch(expected, actual) is None:
            return f"Expected full regex match '{expected}', found '{actual}'"
    else:
        expected = pattern.format_map(tags)
        if operator == "equals":
            if actual != expected:
                return f"Expected '{expected}', found '{actual}'"
        elif operator == "contains":
            if expected not in actual:
                return f"Expected substring '{expected}', found '{actual}'"
        else:
            raise ValueError(f"Unsupported template.operator: {operator}")
    return None

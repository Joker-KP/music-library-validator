from copy import deepcopy

import pytest
import yaml

from music_validator import cli
from music_validator.config import load_config
from music_validator.validation import validate


def rule(**overrides):
    return {
        "id": "credit",
        "field": "comment",
        "when": {"field": "lyricist", "is_present": True},
        "template": {"operator": "contains", "pattern": "słowa: {lyricist}"},
        **overrides,
    }


def row(tags=None, format="flac"):
    return {
        "path": f"/music/A/01 - Song.{format}",
        "album_dir": "/music/A",
        "error": None,
        "metadata": {"tags": tags or {}, "artwork": [], "format": format},
    }


def check(custom, tags=None, format="flac"):
    return [
        i
        for i in validate([row(tags, format)], {"custom_rules": [custom]})
        if i["rule"] == custom["id"]
    ]


@pytest.mark.parametrize("lyricist", [None, "", "   "])
def test_presence_guard_skips_missing_or_blank_template_fields(lyricist):
    tags = {} if lyricist is None else {"lyricist": lyricist}
    assert not check(rule(), tags)


def test_requested_lyricist_comment_contains_example():
    tags = {"lyricist": "Jan Łódzki", "comment": "muzyka: Anna; słowa: Jan Łódzki; live"}
    assert not check(rule(), tags)
    tags["comment"] = "muzyka: Anna"
    issues = check(rule(severity="error"), tags)
    assert len(issues) == 1
    assert issues[0]["severity"] == "error"
    assert "słowa: Jan Łódzki" in issues[0]["message"]


@pytest.mark.parametrize(
    "operator,pattern,actual,passes",
    [
        ("equals", "{artist} - {album}", "A - B", True),
        ("equals", "{artist} - {album}", "prefix A - B", False),
        ("contains", "{artist} - {album}", "prefix A - B suffix", True),
        ("contains", "{artist} - {album}", "prefix A suffix", False),
        ("contains", "Some", "some", False),
        ("regex", r"\d{{2}} - {artist}", "01 - A", True),
        ("regex", r"\d{{2}} - {artist}", "prefix 01 - A suffix", False),
        ("regex", r".*{artist}.*", "prefix A suffix", True),
        ("equals", "", "", True),
        ("regex", "", "", True),
        ("contains", "", "anything", True),
    ],
)
def test_template_operators(operator, pattern, actual, passes):
    custom = rule(when={}, template={"operator": operator, "pattern": pattern})
    assert (not check(custom, {"artist": "A", "album": "B", "comment": actual})) == passes


def test_regex_tag_values_are_literal_not_regex_instructions():
    custom = rule(template={"operator": "regex", "pattern": r"słowa: {lyricist}"})
    tags = {"lyricist": "A (B) [C].*", "comment": "słowa: A (B) [C].*"}
    assert not check(custom, tags)
    tags["comment"] = "słowa: A B Cwhatever"
    assert check(custom, tags)


@pytest.mark.parametrize(
    "condition,tags,format,applies",
    [
        ({"format": "flac"}, {}, "flac", True),
        ({"format": "flac"}, {}, "mp3", False),
        ({"format": "mp3"}, {}, "mp3", True),
        ({"is_compilation": True}, {"compilation": "1"}, "mp3", True),
        ({"is_compilation": True}, {"compilation": " TRUE "}, "flac", True),
        ({"is_compilation": True}, {"compilation": "yes"}, "flac", True),
        ({"is_compilation": True}, {}, "flac", False),
        ({"is_compilation": False}, {}, "flac", True),
        ({"is_compilation": False}, {"compilation": "0"}, "flac", True),
        ({"is_compilation": False}, {"compilation": "False"}, "flac", True),
        ({"is_compilation": False}, {"compilation": "NO"}, "flac", True),
        ({"is_compilation": True}, {"compilation": "unknown"}, "flac", False),
        ({"is_compilation": False}, {"compilation": "unknown"}, "flac", False),
        ({"field": "origlyricist", "is_present": True}, {"origlyricist": "Żaneta"}, "flac", True),
        ({"field": "origlyricist", "is_present": True}, {"origlyricist": "  "}, "flac", False),
        ({"field": "origlyricist", "is_present": False}, {}, "flac", True),
        ({"field": "origlyricist", "is_present": False}, {"origlyricist": "  "}, "flac", True),
        ({"field": "origlyricist", "is_present": False}, {"origlyricist": "0"}, "flac", False),
        (
            {"field": "title", "contains": "some-string"},
            {"title": "prefix some-string suffix"},
            "flac",
            True,
        ),
        ({"field": "title", "contains": "Some"}, {"title": "some"}, "flac", False),
        ({"field": "title", "contains": "[Live]"}, {"title": "Song [Live]"}, "flac", True),
        (
            {
                "format": "flac",
                "is_compilation": True,
                "field": "title",
                "is_present": True,
                "contains": "Live",
            },
            {"compilation": "1", "title": "Live song"},
            "flac",
            True,
        ),
        (
            {"format": "mp3", "field": "title", "contains": "Live"},
            {"title": "Live song"},
            "flac",
            False,
        ),
    ],
)
def test_when_selectors_and_conjunction(condition, tags, format, applies):
    custom = rule(when=condition, template={"operator": "equals", "pattern": "required"})
    issues = check(custom, tags, format)
    assert bool(issues) == applies
    if issues:
        assert issues[0]["severity"] == "warning"


def test_missing_template_value_is_an_explicit_error_only_when_applied():
    custom = rule(when={"format": "flac"})
    issues = check(custom)
    assert len(issues) == 1 and issues[0]["severity"] == "error"
    assert "lyricist" in issues[0]["message"]
    assert not check(custom, format="mp3")


def test_equals_can_be_conditional():
    custom = {
        "id": "artist_comment",
        "field": "comment",
        "template": {"operator": "equals", "pattern": "{artist}"},
        "when": {"format": "flac"},
    }
    assert not check(custom, {"artist": "A", "comment": "A"})
    assert check(custom, {"artist": "A", "comment": "B"})
    assert not check(custom, {"artist": "A", "comment": "B"}, "mp3")


def test_custom_rule_configuration_accepts_structured_templates(tmp_path):
    path = tmp_path / "rules.yaml"
    comparison = {
        "id": "artist_comment",
        "field": "comment",
        "template": {"operator": "equals", "pattern": "{artist}"},
    }
    path.write_text(
        yaml.safe_dump({"library_root": "/music", "custom_rules": [rule(), comparison]})
    )
    config = load_config(path)
    assert config["custom_rules"][0]["template"]["operator"] == "contains"
    assert config["custom_rules"][1]["template"] == {"operator": "equals", "pattern": "{artist}"}


def test_removed_equals_template_is_rejected(tmp_path):
    path = tmp_path / "rules.yaml"
    custom = {"id": "obsolete", "field": "comment", "equals_template": "{artist}"}
    path.write_text(yaml.safe_dump({"library_root": "/music", "custom_rules": [custom]}))
    with pytest.raises(ValueError, match="equals_template"):
        load_config(path)


def test_regex_templates_validate_escaped_quantifiers(tmp_path):
    path = tmp_path / "rules.yaml"
    custom = rule(when={}, template={"operator": "regex", "pattern": r"^\d{{2}} - {artist}$"})
    path.write_text(yaml.safe_dump({"library_root": "/music", "custom_rules": [custom]}))
    loaded = load_config(path)["custom_rules"][0]
    assert not check(loaded, {"artist": "A (B)", "comment": "01 - A (B)"})


def test_runtime_regex_error_becomes_an_issue():
    custom = rule(when={}, template={"operator": "regex", "pattern": "{lyricist}+"})
    issues = check(custom, {"lyricist": "", "comment": "anything"})
    assert len(issues) == 1 and issues[0]["severity"] == "error"


def test_rendered_regex_limit_is_an_error_not_a_pass():
    custom = rule(when={}, template={"operator": "regex", "pattern": "{lyricist}"})
    issues = check(custom, {"lyricist": "x" * 4097})
    assert len(issues) == 1 and issues[0]["severity"] == "error"
    assert "4096" in issues[0]["message"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"when": None},
        {"when": []},
        {"when": {"unknown": True}},
        {"when": {"format": "wav"}},
        {"when": {"format": ["flac"]}},
        {"when": {"is_compilation": "true"}},
        {"when": {"is_present": True}},
        {"when": {"contains": "x"}},
        {"when": {"field": "title"}},
        {"when": {"field": "title", "is_present": "yes"}},
        {"when": {"field": "title", "contains": True}},
        {"when": {"field": "title", "contains": ""}},
        {"when": {"field": "title.__class__", "is_present": True}},
        {"template": None},
        {"template": {}},
        {"template": {"pattern": "x"}},
        {"template": {"operator": "equals"}},
        {"template": {"operator": "eval", "pattern": "x"}},
        {"template": {"operator": "equal", "pattern": "x"}},
        {"template": {"operator": "equals", "pattern": 1}},
        {"template": {"operator": "contains", "pattern": "{title[0]}"}},
        {"template": {"operator": "regex", "pattern": "{title.__class__}"}},
        {"template": {"operator": "regex", "pattern": "("}},
        {"template": {"operator": "regex", "pattern": r"\d{2}"}},
        {"template": {"operator": "equals", "unknown": True, "pattern": "x"}},
        {"equals_template": "also legacy"},
    ],
)
def test_invalid_custom_rule_fails_before_writes(tmp_path, overrides, capsys):
    custom = rule(**deepcopy(overrides))
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"library_root": "/music", "custom_rules": [custom]}))
    assert (
        cli.main(
            [
                "scan",
                "--config",
                str(path),
                "--db",
                str(tmp_path / "cache.sqlite"),
                "--reports",
                str(tmp_path / "reports"),
            ]
        )
        == 2
    )
    assert "ERROR:" in capsys.readouterr().err
    assert not (tmp_path / "cache.sqlite").exists()
    assert not (tmp_path / "reports").exists()

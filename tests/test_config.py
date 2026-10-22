import pytest
import yaml

from music_validator import cli
from music_validator.config import load_config


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"library_root": "/x", "artwork": None},
        {"library_root": "/x", "filename": {"regex": "("}},
        {"library_root": "/x", "filename": {"allowed_chars": []}},
        {"library_root": "/x", "filename": {"allowed_chars": True}},
        {"library_root": "/x", "filename": {"allowed_chars": "z-a"}},
        {"library_root": "/x", "filename": {"allowed_chars": "x" * 4097}},
        {"library_root": "/x", "extensions": [".wav"]},
        {"library_root": "/x", "album_rules": {"unique_disc_track": "yes"}},
        {"library_root": "/x", "artwork": {"min_width": -1}},
        {"library_root": "/x", "unknown": True},
        {"library_root": "/x", "custom_rules": [{"id": "a", "field": "comment"}]},
    ],
)
def test_invalid_config_has_clear_cli_error(tmp_path, value, capsys):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(value))
    assert (
        cli.main(
            [
                "scan",
                "--config",
                str(path),
                "--db",
                str(tmp_path / "cache"),
                "--reports",
                str(tmp_path / "reports"),
            ]
        )
        == 2
    )
    assert "ERROR:" in capsys.readouterr().err
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize(
    "template",
    [
        "{title.missing}",
        "{title[100]}",
        "{title!r}",
        "{tracknumber:999999999d}",
        "{title:{artist}}",
        "{",
        "{0}",
    ],
)
def test_restrict_template_grammar(tmp_path, template):
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump({"library_root": "/x", "filename": {"match_template": template}})
    )
    with pytest.raises(ValueError, match="template"):
        load_config(path)


def test_defaults_and_explicit_disabling(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("library_root: /x\n")
    config = load_config(path)
    assert config["required_tags"] == ["title", "artist", "album", "tracknumber", "comment"]
    assert config["artwork"]["required"] is True
    assert config["filename"]["allowed_chars"] == "[]0-9a-zA-Z -()"
    assert config["cross_album_rules"] == {
        "enabled": True,
        "severity": "warning",
        "fields": ["composer", "lyricist", "origlyricist"],
        "artist_alias_groups": [],
    }
    path.write_text("library_root: /x\nrequired_tags: []\nartwork: {required: false}\n")
    assert load_config(path)["required_tags"] == []
    assert load_config(path)["artwork"]["required"] is False


@pytest.mark.parametrize("value", ["", None, "[]0-9a-zA-Z -()", "a-f []-", "a-ząćęłńóśźż"])
def test_allowed_characters_config(tmp_path, value):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"library_root": "/x", "filename": {"allowed_chars": value}}))
    assert load_config(path)["filename"]["allowed_chars"] == value


def test_duplicate_yaml_keys_rejected(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("library_root: /x\nartwork: {required: true, required: false}\n")
    with pytest.raises(ValueError, match="Duplicate"):
        load_config(path)


@pytest.mark.parametrize(
    "options",
    [
        None,
        [],
        {"unknown": True},
        {"enabled": "yes"},
        {"severity": "fatal"},
        {"fields": None},
        {"fields": "composer"},
        {"fields": True},
        {"fields": {}},
        {"fields": [None]},
        {"fields": [1]},
        {"fields": ["Composer"]},
        {"fields": [""]},
        {"fields": [" composer "]},
        {"fields": ["composer", "composer"]},
        {"fields": ["composer); DROP TABLE tracks; --"]},
        {"artist_alias_groups": None},
        {"artist_alias_groups": "A"},
        {"artist_alias_groups": ["A", "B"]},
        {"artist_alias_groups": [[]]},
        {"artist_alias_groups": [["A"]]},
        {"artist_alias_groups": [["A", None]]},
        {"artist_alias_groups": [["A", 1]]},
        {"artist_alias_groups": [["A", " "]]},
        {"artist_alias_groups": [["A", "A"]]},
        {"artist_alias_groups": [["A", "B"], ["B", "C"]]},
        {"artist_alias_groups": [[" A ", "B"], ["A", "C"]]},
    ],
)
def test_invalid_cross_album_config_fails_before_outputs(tmp_path, options, capsys):
    test_invalid_config_has_clear_cli_error(
        tmp_path, {"library_root": "/x", "cross_album_rules": options}, capsys
    )


def test_cross_album_partial_settings_inherit_defaults(tmp_path):
    path = tmp_path / "rules.yaml"
    options = {"artist_alias_groups": [["Wolna Grupa Bukowina", "Wojciech Bellon"]]}
    path.write_text(yaml.safe_dump({"library_root": "/x", "cross_album_rules": options}))
    config = load_config(path)["cross_album_rules"]
    assert config["enabled"] is True and config["severity"] == "warning"
    assert config["fields"] == ["composer", "lyricist", "origlyricist"]
    assert config["artist_alias_groups"] == options["artist_alias_groups"]
    path.write_text("library_root: /x\ncross_album_rules: {enabled: false}\n")
    assert load_config(path)["cross_album_rules"]["enabled"] is False


@pytest.mark.parametrize("fields", [[], ["composer"], ["genre", "comment"]])
def test_cross_album_fields_accept_normalized_names_and_empty_list(tmp_path, fields):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"library_root": "/x", "cross_album_rules": {"fields": fields}}))
    assert load_config(path)["cross_album_rules"]["fields"] == fields

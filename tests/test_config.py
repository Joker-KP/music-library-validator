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
    path.write_text("library_root: /x\nrequired_tags: []\nartwork: {required: false}\n")
    assert load_config(path)["required_tags"] == []
    assert load_config(path)["artwork"]["required"] is False


def test_duplicate_yaml_keys_rejected(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("library_root: /x\nartwork: {required: true, required: false}\n")
    with pytest.raises(ValueError, match="Duplicate"):
        load_config(path)

import hashlib
import os
import shutil
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, TCMP, TEXT, TOLY


@pytest.fixture(autouse=True)
def subprocess_source_path(monkeypatch):
    source = str(Path(__file__).resolve().parents[1] / "src")
    monkeypatch.setenv("PYTHONPATH", source + os.pathsep + os.environ.get("PYTHONPATH", ""))


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "music"
    for disc, ext in [(1, "mp3"), (2, "flac")]:
        path = root / "艺术家" / "Żółć" / f"CD{disc}" / f"01 - Łódź.{ext}"
        path.parent.mkdir(parents=True)
        shutil.copyfile(Path(__file__).parent / "fixtures" / f"silence.{ext}", path)
    return root


@pytest.fixture
def config_file(tmp_path, library):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"library_root": str(library)}), encoding="utf-8")
    return path


@pytest.fixture
def custom_rule_library(tmp_path):
    """Create tagged synthetic files in memory, then write new fixtures only."""
    root = tmp_path / "custom-music"
    for ext in ("mp3", "flac"):
        source = BytesIO((Path(__file__).parent / "fixtures" / f"silence.{ext}").read_bytes())
        if ext == "mp3":
            tags = ID3(source)
            tags.add(TEXT(encoding=3, text=["Żaneta Nowak"]))
            tags.add(TOLY(encoding=3, text=["Jan Łódzki"]))
            tags.add(TCMP(encoding=3, text=["1"]))
            tags.delall("COMM")
            tags.add(
                COMM(
                    encoding=3,
                    lang="pol",
                    desc="",
                    text=["muzyka: Anna; słowa: Żaneta Nowak; live"],
                )
            )
            tags.save(source)
        else:
            audio = FLAC(source)
            audio["lyricist"] = ["Żaneta Nowak"]
            audio["originallyricist"] = ["Jan Łódzki"]
            audio["compilation"] = ["0"]
            audio["comment"] = ["muzyka: Anna"]
            source.seek(0)
            audio.save(source)
        path = root / ext.upper() / f"01 - Song.{ext}"
        path.parent.mkdir(parents=True)
        path.write_bytes(source.getvalue())
    return root


def signatures(root):
    return {
        str(p.relative_to(root)): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_size,
            p.stat().st_mtime_ns,
        )
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }

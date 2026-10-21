import hashlib
import os
import shutil
from pathlib import Path

import pytest
import yaml


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

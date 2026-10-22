"""Directory-scoped declarative patches and read-only configuration snapshots."""

import os
import stat
from copy import deepcopy

import yaml

from .config import ConfigLoader, _name, _object, validate_config
from .grouping import album_directory

LOCAL_FILENAME = "rules.yaml"
PATCH_KEYS = {
    "added_required_tags",
    "removed_required_tags",
    "added_custom_rules",
    "removed_custom_rules",
    "artwork",
    "album_rules",
}


def _names(value, where):
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list")  # noqa: TRY004
    for name in value:
        _name(name, f"{where} entry")
    if len(set(value)) != len(value):
        raise ValueError(f"{where} must not contain duplicates")
    return value


def apply_patch(parent, patch):
    """Apply removals then additions without mutating inherited configuration."""
    _object(patch, PATCH_KEYS, "local rules")
    added = _names(patch.get("added_required_tags", []), "added_required_tags")
    removed = _names(patch.get("removed_required_tags", []), "removed_required_tags")
    if set(added) & set(removed):
        raise ValueError("Cannot add and remove the same required tag in one patch")
    removed_rules = _names(patch.get("removed_custom_rules", []), "removed_custom_rules")
    unknown = set(removed_rules) - {r["id"] for r in parent["custom_rules"]}
    if unknown:
        raise ValueError(f"Cannot remove unknown inherited custom rule IDs: {sorted(unknown)}")
    added_rules = patch.get("added_custom_rules", [])
    if not isinstance(added_rules, list):
        raise ValueError("added_custom_rules must be a list")  # noqa: TRY004
    effective = deepcopy(parent)
    required = effective["required_tags"] = [
        name for name in parent["required_tags"] if name not in removed
    ]
    required.extend(name for name in added if name not in required)
    effective["custom_rules"] = [
        rule for rule in effective["custom_rules"] if rule["id"] not in removed_rules
    ] + deepcopy(added_rules)
    for section in ("artwork", "album_rules"):
        if section in patch:
            _object(patch[section], set(parent[section]), f"local {section}")
            effective[section].update(patch[section])
    # Reuse the main schema for types, unique IDs, conditions and template safety.
    return validate_config(effective)


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read(directory, dir_fd):
    """Read only a regular, non-symlink config relative to its verified parent."""
    path = directory / LOCAL_FILENAME
    try:
        info = os.stat(LOCAL_FILENAME, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{path}: local configuration must be a regular file, not a symlink")
    fd = os.open(LOCAL_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or _signature(before) != _signature(info):
            raise ValueError(f"{path}: local configuration changed while opening; retry scan")
        contents = source.read()
        after = os.fstat(source.fileno())
        linked = os.stat(LOCAL_FILENAME, dir_fd=dir_fd, follow_symlinks=False)
        if _signature(before) != _signature(after) or _signature(after) != _signature(linked):
            raise ValueError(f"{path}: local configuration changed while reading; retry scan")
    return _signature(after), contents


class LocalRules:
    """Keep effective configs only at patch directories, independent of audio cache."""

    def __init__(self, root, config, primary_path=None):
        self.root = root
        self.base = config
        self.primary_path = primary_path
        self.configs = {}
        self.snapshots = {}
        self.visited = set()

    def for_directory(self, directory):
        if ".." in directory.parts or not directory.is_relative_to(self.root):
            raise ValueError(f"Configuration directory {directory} is outside library root")
        while directory not in self.configs:
            if directory == self.root:
                return self.base
            directory = directory.parent
        return self.configs[directory]

    def collect(self, directory, dir_fd):
        path = directory / LOCAL_FILENAME
        try:
            snapshot = _read(directory, dir_fd)
            if snapshot is None:
                return
            self.snapshots[directory] = snapshot
            # An explicitly selected main config is global, even if inside the library.
            if path == self.primary_path:
                return
            patch = yaml.load(snapshot[1].decode("utf-8"), Loader=ConfigLoader)
            effective = apply_patch(self.for_directory(directory), patch)
            album = album_directory(
                directory / "_track.mp3", self.root, self.base["disc_folder_pattern"]
            )
            if "album_rules" in patch and album != directory:
                raise ValueError(f"Put album_rules in the album directory {album}")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
        self.configs[directory] = effective

    def check_directory(self, directory, dir_fd):
        try:
            current = _read(directory, dir_fd)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"{directory / LOCAL_FILENAME}: configuration changed; retry scan"
            ) from exc
        if current != self.snapshots.get(directory):
            raise ValueError(f"{directory / LOCAL_FILENAME}: configuration changed; retry scan")
        if current is not None:
            self.visited.add(directory)

    def verify(self):
        """Detect vanished patch directories and edits after their processing visit."""
        missing = self.snapshots.keys() - self.visited
        if missing:
            path = min(missing) / LOCAL_FILENAME
            raise ValueError(f"{path}: configuration directory changed; retry scan")
        for directory in self.snapshots:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for component in directory.relative_to(self.root).parts:
                    child = os.open(
                        component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                    )
                    os.close(fd)
                    fd = child
                self.check_directory(directory, fd)
            except OSError as exc:
                raise ValueError(
                    f"{directory / LOCAL_FILENAME}: configuration changed; retry scan"
                ) from exc
            finally:
                os.close(fd)

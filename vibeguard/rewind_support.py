"""Filesystem and Git tree helpers shared by checkpoint operations."""

import os
from pathlib import Path
import subprocess
from typing import Dict, List, Optional, Set, Tuple


class CheckpointSupport:
    """Safe path handling and Git metadata access for CheckpointManager."""

    root: Path

    def _git_index_path(self) -> Path:
        raw = os.fsdecode(self._git(["rev-parse", "--git-path", "index"]).rstrip(b"\n"))
        path = Path(raw)
        return path if path.is_absolute() else self.root / path

    @staticmethod
    def _internal_path(rel: str) -> bool:
        return any(part in (".git", ".vibeguard") for part in Path(rel).parts)

    @classmethod
    def _file_entries(cls, root: Path) -> List[str]:
        """Enumerate files and links without following directory symlinks."""
        entries = []
        for directory, dirs, files in os.walk(str(root), followlinks=False):
            for name in list(dirs):
                path = Path(directory) / name
                rel = str(path.relative_to(root))
                if cls._internal_path(rel):
                    dirs.remove(name)
                elif path.is_symlink():
                    entries.append(rel)
                    dirs.remove(name)
            for name in files:
                path = Path(directory) / name
                rel = str(path.relative_to(root))
                if not cls._internal_path(rel):
                    entries.append(rel)
        return entries

    def _destination(self, rel: str) -> Path:
        """Replace obstructing links/files rather than traversing them."""
        relative = Path(rel)
        if relative.is_absolute() or ".." in relative.parts or self._internal_path(rel):
            raise ValueError("Unsafe checkpoint path: %r" % rel)
        parent = self.root
        for part in relative.parts[:-1]:
            parent = parent / part
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                parent.unlink()
            parent.mkdir(exist_ok=True)
        destination = self.root / relative
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            for directory, dirs, files in os.walk(str(destination), followlinks=False):
                if files or any((Path(directory) / name).is_symlink() for name in dirs):
                    raise ValueError("Cannot replace non-empty directory %s; move its files before rewinding" % destination)
            for directory, _dirs, _files in os.walk(str(destination), topdown=False, followlinks=False):
                Path(directory).rmdir()
        return destination

    def _remove_entry(self, rel: str) -> bool:
        relative = Path(rel)
        if relative.is_absolute() or ".." in relative.parts or self._internal_path(rel):
            return False
        parent = self.root
        for part in relative.parts[:-1]:
            parent = parent / part
            if parent.is_symlink() or not parent.is_dir():
                return False
        path = self.root / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
            return True
        return False

    def _validate_restore_collisions(self, target_files: Set[str], captured_files: Set[str]) -> None:
        """Refuse to overwrite entries absent from the safety backup."""
        for rel in target_files:
            path = self.root
            parts = Path(rel).parts
            for position, part in enumerate(parts):
                path = path / part
                if path.is_symlink() or (path.exists() and not path.is_dir()):
                    existing = str(path.relative_to(self.root))
                    if existing not in captured_files:
                        raise ValueError("Cannot overwrite %r: ignored or otherwise not captured in safety backup; move it before rewinding" % existing)
                    break
                if position == len(parts) - 1 and path.is_dir():
                    for directory, dirs, files in os.walk(str(path), followlinks=False):
                        for name in dirs:
                            child = Path(directory) / name
                            if name in (".git", ".vibeguard"):
                                raise ValueError("Cannot overwrite directory %r: internal metadata not captured in safety backup" % rel)
                            if child.is_symlink():
                                files.append(name)
                        for name in files:
                            existing = str((Path(directory) / name).relative_to(self.root))
                            if existing not in captured_files:
                                raise ValueError("Cannot overwrite %r: ignored or otherwise not captured in safety backup; move it before rewinding" % existing)

    def _tree_entries(self, commit: str) -> List[Tuple[str, str, str, str]]:
        entries = []
        for record in self._git(["ls-tree", "-rz", commit]).split(b"\0"):
            if record:
                header, raw_path = record.split(b"\t", 1)
                mode, kind, oid = header.decode("ascii").split()
                path = os.fsdecode(raw_path)
                if not self._internal_path(path):
                    entries.append((path, mode, kind, oid))
        return entries

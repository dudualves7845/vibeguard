"""Snapshot engine: Captures baseline architecture signatures to detect AI drift."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import fnmatch
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Dict, List, Optional, Set, Tuple

from .analyzer import CodeAnalyzer, FileAnalysis
from .config import GuardConfig


@dataclass
class ProjectSnapshot:
    """Represents a frozen baseline of code structure and symbols."""
    timestamp: float
    total_files: int
    files: Dict[str, Dict]  # rel_path -> serialized FileAnalysis

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "total_files": self.total_files,
            "files": self.files,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ProjectSnapshot":
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            raise ValueError("Invalid baseline: 'files' must be an object")
        for path, info in data["files"].items():
            if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError("Invalid baseline file path")
            if not isinstance(info, dict) or not isinstance(info.get("symbols", []), list):
                raise ValueError("Invalid baseline file metadata")
            for symbol in info.get("symbols", []):
                if not isinstance(symbol, dict) or not isinstance(symbol.get("name"), str):
                    raise ValueError("Invalid baseline symbol metadata")
        return cls(
            timestamp=data.get("timestamp", 0.0),
            total_files=data.get("total_files", 0),
            files=data.get("files", {}),
        )


def _analyze_single_file(args: Tuple[Path, Path]) -> Optional[Tuple[str, Dict]]:
    """Helper for concurrent file analysis."""
    root_dir, filepath = args
    try:
        rel_path = str(filepath.relative_to(root_dir))
        try:
            content = filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = filepath.read_text(encoding="latin-1")

        analysis = CodeAnalyzer.analyze_file(rel_path, content)
        data = asdict(analysis)
        return rel_path, data
    except Exception as exc:
        raise ValueError(f"Cannot analyze protected file {filepath}: {exc}") from exc


class SnapshotEngine:
    """Manages creation, storage, and retrieval of architecture snapshots."""

    def __init__(self, root_dir: str, config: Optional[GuardConfig] = None):
        self.root_dir = Path(root_dir).resolve()
        self.config = config or GuardConfig.load_from_file(str(self.root_dir / ".vibeguard.json"))
        self.store_dir = self.root_dir / ".vibeguard"

    def scan_files(self) -> List[Path]:
        """Finds all files in repo matching protected patterns and not ignored."""
        found = []
        for root, dirs, files in os.walk(self.root_dir):
            rel_root = os.path.relpath(root, self.root_dir)
            if rel_root == ".":
                rel_root = ""

            # Check directory ignore patterns
            dirs[:] = [
                d for d in dirs
                if not any(
                    fnmatch.fnmatch(os.path.join(rel_root, d), pat.rstrip("/*"))
                    or fnmatch.fnmatch(d, pat.rstrip("/*"))
                    or fnmatch.fnmatch(os.path.join(rel_root, d) + "/*", pat)
                    or fnmatch.fnmatch(d, pat)
                    for pat in self.config.ignored_paths
                )
            ]

            for file in files:
                rel_path = os.path.normpath(os.path.join(rel_root, file))
                # Check if ignored
                if any(
                    fnmatch.fnmatch(rel_path, pat)
                    or fnmatch.fnmatch(file, pat)
                    or any(fnmatch.fnmatch(part, pat.rstrip("/*")) for part in Path(rel_path).parts)
                    for pat in self.config.ignored_paths
                ):
                    continue
                # Check if protected
                if any(fnmatch.fnmatch(file, pat) or fnmatch.fnmatch(rel_path, pat) for pat in self.config.protected_paths):
                    found.append(self.root_dir / rel_path)

        return sorted(found)

    def create_snapshot(
        self,
        file_filter: Optional[Set[str]] = None,
        max_workers: Optional[int] = None,
    ) -> ProjectSnapshot:
        """Analyzes all protected files and creates a snapshot concurrently.
        
        If file_filter is provided, only files whose relative path matches one of the
        paths in file_filter will be analyzed.
        """
        target_files = self.scan_files()
        items_to_analyze: List[Tuple[Path, Path]] = []

        for filepath in target_files:
            try:
                rel_path = str(filepath.relative_to(self.root_dir))
            except ValueError:
                continue
            if file_filter is not None and rel_path not in file_filter:
                continue
            items_to_analyze.append((self.root_dir, filepath))

        files_data: Dict[str, Dict] = {}
        # Concurrently analyze files using ThreadPoolExecutor
        if len(items_to_analyze) <= 4:
            # For very small file counts, process directly without thread overhead
            for item in items_to_analyze:
                res = _analyze_single_file(item)
                if res:
                    files_data[res[0]] = res[1]
        else:
            workers = max_workers or min(32, (os.cpu_count() or 4) * 2)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = executor.map(_analyze_single_file, items_to_analyze)
                for res in results:
                    if res:
                        files_data[res[0]] = res[1]

        snapshot = ProjectSnapshot(
            timestamp=time.time(),
            total_files=len(files_data),
            files=files_data,
        )
        return snapshot

    def save_baseline(self, snapshot: Optional[ProjectSnapshot] = None) -> Path:
        """Persists current snapshot as the canonical baseline."""
        if snapshot is None:
            snapshot = self.create_snapshot()
        self.store_dir.mkdir(parents=True, exist_ok=True)
        baseline_file = self.store_dir / "baseline.json"
        fd, temporary = tempfile.mkstemp(prefix="baseline-", suffix=".tmp", dir=str(self.store_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, baseline_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return baseline_file

    def load_baseline(self) -> Optional[ProjectSnapshot]:
        """Loads canonical baseline snapshot if exists."""
        baseline_file = self.store_dir / "baseline.json"
        if not baseline_file.exists():
            return None
        try:
            with open(baseline_file, "r", encoding="utf-8") as f:
                return ProjectSnapshot.from_dict(json.load(f))
        except (json.JSONDecodeError, OSError, ValueError, KeyError) as exc:
            raise ValueError(f"Cannot read baseline {baseline_file}: {exc}") from exc

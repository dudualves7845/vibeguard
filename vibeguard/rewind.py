"""Vibe Rewind: AI Session Checkpoint and Time Machine Engine.

Captures working tree files and Git index state separately before and during
AI coding sessions, retaining disk backups for exact, safe restoration.
Supports safe rollback, safety restore backups, diff inspection, and history.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .git_utils import GitIntegration
from .rewind_support import CheckpointSupport

RESET, BOLD, RED, GREEN = "\033[0m", "\033[1m", "\033[31m", "\033[32m"
YELLOW, BLUE, CYAN, GRAY = "\033[33m", "\033[34m", "\033[36m", "\033[90m"


def _color(text: str, color: str) -> str:
    return text if os.environ.get("NO_COLOR") else f"{color}{text}{RESET}"


def _format_relative_time(ts: float) -> str:
    diff = max(0, int(time.time() - ts))
    if diff < 60:
        return f"{diff}s ago"
    elif diff < 3600:
        return f"{diff // 60}m ago"
    elif diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


@dataclass
class CheckpointMetadata:
    """Metadata describing a saved AI session checkpoint."""
    id: str
    timestamp: float
    created_at: str
    message: str
    is_safety: bool = False
    git_commit: Optional[str] = None
    git_ref: Optional[str] = None
    files_count: int = 0
    changed_files: List[str] = field(default_factory=list)
    untracked_files: List[str] = field(default_factory=list)
    drift_status: str = "unknown"
    git_index_saved: bool = False
    git_worktree_saved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointMetadata":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CheckpointManager(CheckpointSupport):
    """Manages checkpoints and time-machine rewinds for VibeGuard."""

    def __init__(self, repo_root: Optional[str] = None):
        self.root = GitIntegration(repo_root or os.getcwd()).get_git_root()
        self.vibeguard_dir = self.root / ".vibeguard"
        self.checkpoints_dir = self.vibeguard_dir / "checkpoints"
        self.index_file = self.checkpoints_dir / "index.json"
        self.snapshots_dir = self.checkpoints_dir / "snapshots"
        self._ensure_dirs()

    def _git(self, args: List[str], env: Optional[Dict[str, str]] = None) -> bytes:
        return subprocess.check_output(["git"] + args, cwd=str(self.root), env=env, stderr=subprocess.PIPE)

    def _ensure_dirs(self) -> None:
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def is_git_repo(self) -> bool:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    def _load_index(self) -> List[CheckpointMetadata]:
        if not self.index_file.exists():
            return []
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            items = data.get("checkpoints", []) if isinstance(data, dict) else data
            return [CheckpointMetadata.from_dict(it) for it in items if isinstance(it, dict)]
        except Exception:
            return []

    def _save_index(self, items: List[CheckpointMetadata]) -> None:
        payload = {"version": 1, "updated_at": datetime.now().isoformat(), "checkpoints": [it.to_dict() for it in items]}
        self.index_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _evaluate_drift_health(self) -> str:
        try:
            from .drift import DriftEngine
            engine = DriftEngine(str(self.root))
            baseline = engine.snapshot_engine.load_baseline()
            if not baseline:
                return "unanchored"
            report = engine.evaluate(baseline=baseline)
            return "drift_detected" if report.total_findings > 0 else "clean"
        except Exception:
            return "unknown"

    def create_checkpoint(self, message: Optional[str] = None, is_safety: bool = False, evaluate_drift: bool = True) -> CheckpointMetadata:
        now = time.time()
        dt_str = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
        prefix = "safety" if is_safety else "cp"
        short_rand = os.urandom(3).hex()
        cp_id = f"{prefix}_{dt_str}_{short_rand}"
        msg = (message or ("Pre-rewind safety restore point" if is_safety else "AI Session Checkpoint")).strip()

        changed_files, untracked_files = [], []
        git_commit, git_ref = None, None
        git_index_saved, git_worktree_saved = False, False

        if self.is_git_repo():
            git_dir = GitIntegration(str(self.root)).get_git_dir()
            if git_dir is None:
                raise RuntimeError("Cannot resolve Git directory for checkpoint")
            cp_dir = self.snapshots_dir / cp_id
            cp_dir.mkdir()
            index_path = self._git_index_path()
            if index_path.exists():
                shutil.copy2(str(index_path), str(cp_dir / "git-index"))
                index_env = os.environ.copy()
                index_env["GIT_INDEX_FILE"] = str(cp_dir / "git-index")
                # Materialize split-index references in the backup so cleaning
                # sharedindex files later cannot make the checkpoint unusable.
                self._git(["update-index", "--no-split-index"], env=index_env)
            else:
                (cp_dir / "git-index-absent").touch()
            # Keep every referenced blob recoverable, including conflict stages
            # and content present only in the index, even after Git pruning.
            objects_dir = cp_dir / "index-objects"
            objects_dir.mkdir()
            for record in self._git(["ls-files", "--stage", "-z"]).split(b"\0"):
                if not record:
                    continue
                header = record.split(b"\t", 1)[0].split()
                mode, oid = header[0], header[1].decode("ascii")
                if mode != b"160000" and oid.strip("0"):
                    blob = objects_dir / oid
                    if not blob.exists():
                        blob.write_bytes(self._git(["cat-file", "blob", oid]))
            git_index_saved = True
            fd, tmp_path = tempfile.mkstemp(prefix="vg_idx_", dir=str(git_dir))
            os.close(fd)
            tmp_index = Path(tmp_path)
            tmp_index.unlink()
            try:
                env = os.environ.copy()
                env["GIT_INDEX_FILE"] = str(tmp_index)
                has_head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False).returncode == 0
                self._git(["read-tree", "HEAD"] if has_head else ["read-tree", "--empty"], env=env)
                self._git(["add", "-A", "--", ".", ":(top,exclude).vibeguard"], env=env)
                self._git(["rm", "-r", "--cached", "--ignore-unmatch", "--", ".vibeguard"], env=env)
                tree = subprocess.check_output(["git", "write-tree"], cwd=str(self.root), env=env, text=True).strip()
                cmd = ["git", "commit-tree", tree, "-m", f"VibeGuard checkpoint: {msg}"]
                if has_head:
                    cmd.extend(["-p", "HEAD"])
                git_commit = subprocess.check_output(cmd, cwd=str(self.root), env=env, text=True).strip()
                git_ref = f"refs/vibeguard/checkpoints/{cp_id}"
                subprocess.run(["git", "update-ref", git_ref, git_commit], cwd=str(self.root), check=True)
                # Store disk bytes as well: Git filters/CRLF normalization must
                # not silently change the working tree when restoring.
                files_dir = cp_dir / "files"
                files_dir.mkdir()
                for rel, mode, kind, oid in self._tree_entries(git_commit):
                    if kind == "blob":
                        source = self.root / rel
                        destination = files_dir / rel
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(source), str(destination), follow_symlinks=False)
                git_worktree_saved = True
                helper = GitIntegration(str(self.root))
                _, changed_files = helper._parse_porcelain_v1_status()
                untracked_files = [os.fsdecode(item) for item in self._git(["ls-files", "--others", "--exclude-standard", "-z"]).split(b"\0") if item]
                untracked_files = [name for name in untracked_files if not self._internal_path(name)]
                changed_files = [name for name in changed_files if name not in untracked_files and not self._internal_path(name)]
            finally:
                if tmp_index.exists():
                    try:
                        tmp_index.unlink()
                    except Exception:
                        pass
        else:
            cp_dir = self.snapshots_dir / cp_id
            cp_dir.mkdir(parents=True, exist_ok=True)
            for rel in self._file_entries(self.root):
                dest = cp_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(self.root / rel), str(dest), follow_symlinks=False)
                changed_files.append(rel)

        drift_status = self._evaluate_drift_health() if evaluate_drift else "unknown"
        meta = CheckpointMetadata(
            id=cp_id, timestamp=now, created_at=datetime.fromtimestamp(now).isoformat(),
            message=msg, is_safety=is_safety, git_commit=git_commit, git_ref=git_ref,
            files_count=len(changed_files) + len(untracked_files),
            changed_files=sorted(list(set(changed_files))),
            untracked_files=sorted(list(set(untracked_files))),
            drift_status=drift_status,
            git_index_saved=git_index_saved,
            git_worktree_saved=git_worktree_saved,
        )

        all_items = self._load_index()
        all_items.insert(0, meta)
        self._save_index(all_items)
        return meta

    def list_checkpoints(self, limit: int = 50) -> List[CheckpointMetadata]:
        return self._load_index()[:limit]

    def get_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointMetadata]:
        for cp in self._load_index():
            if cp.id == checkpoint_id:
                return cp
        return None

    def get_diff(self, checkpoint_id: str) -> str:
        cp = self.get_checkpoint(checkpoint_id)
        if not cp:
            raise ValueError(f"Checkpoint not found: {checkpoint_id}")
        if self.is_git_repo() and cp.git_commit:
            res = subprocess.run(["git", "diff", cp.git_commit], cwd=str(self.root), stdout=subprocess.PIPE, text=True, check=False)
            return res.stdout
        return ""

    def _remove_new_git_paths(self, target_files: Set[str], safety_files: Set[str], keep_untracked: bool) -> Set[str]:
        tracked = {os.fsdecode(item) for item in self._git(["ls-files", "-z"]).split(b"\0") if item}
        candidates = tracked | safety_files
        if not keep_untracked:
            candidates.update(os.fsdecode(item) for item in self._git(
                ["ls-files", "--others", "--exclude-standard", "-z"]).split(b"\0") if item)
        else:
            candidates = tracked
        deletions = {
            rel for rel in candidates - target_files
            if not self._internal_path(rel)
            and ((self.root / rel).is_file() or (self.root / rel).is_symlink())
        }
        self._validate_restore_collisions(target_files | deletions, safety_files)
        removed = set()
        for rel in sorted(candidates - target_files, key=lambda name: len(Path(name).parts), reverse=True):
            if self._remove_entry(rel):
                removed.add(rel)
        return removed

    def _restore_git_files(self, target: CheckpointMetadata, entries: List[Tuple[str, str, str, str]]) -> Set[str]:
        restored = set()
        files_dir = self.snapshots_dir / target.id / "files"
        for rel, mode, kind, oid in entries:
            if kind != "blob":
                continue  # Gitlinks are separate repositories, never overwrite them.
            destination = self._destination(rel)
            if target.git_worktree_saved:
                shutil.copy2(str(files_dir / rel), str(destination), follow_symlinks=False)
            elif mode == "120000":
                destination.symlink_to(os.fsdecode(self._git(["cat-file", "blob", oid])))
            else:
                destination.write_bytes(self._git(["cat-file", "blob", oid]))
                destination.chmod(0o755 if mode == "100755" else 0o644)
            restored.add(rel)
        return restored

    def _restore_git_index(self, target: CheckpointMetadata) -> None:
        if not target.git_index_saved:
            self._git(["reset"])
            return
        cp_dir = self.snapshots_dir / target.id
        for blob in (cp_dir / "index-objects").iterdir():
            subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(self.root),
                           input=blob.read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        index_path = self._git_index_path()
        backup = cp_dir / "git-index"
        if not backup.exists():
            if index_path.exists():
                index_path.unlink()
            return
        fd, temporary = tempfile.mkstemp(prefix="vg_restore_", dir=str(index_path.parent))
        os.close(fd)
        try:
            shutil.copy2(str(backup), temporary)
            os.replace(temporary, str(index_path))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _rewind_git_checkpoint(
        self, target: CheckpointMetadata, safety: CheckpointMetadata, keep_untracked: bool
    ) -> Tuple[Set[str], Set[str]]:
        entries = self._tree_entries(target.git_commit)
        target_files = {entry[0] for entry in entries}
        safety_files = {entry[0] for entry in self._tree_entries(safety.git_commit)}
        removed = self._remove_new_git_paths(target_files, safety_files, keep_untracked)
        restored = self._restore_git_files(target, entries)
        self._restore_git_index(target)
        return restored, removed

    def _rewind_filesystem_checkpoint(
        self, checkpoint_id: str, keep_untracked: bool
    ) -> Tuple[Set[str], Set[str]]:
        directory = self.snapshots_dir / checkpoint_id
        if not directory.exists():
            raise ValueError(f"Snapshot directory missing for non-git checkpoint: {checkpoint_id}")
        restored = set(self._file_entries(directory))
        removed = set()
        if not keep_untracked:
            for rel in self._file_entries(self.root):
                if rel not in restored and self._remove_entry(rel):
                    removed.add(rel)
        for rel in sorted(restored):
            destination = self._destination(rel)
            shutil.copy2(str(directory / rel), str(destination), follow_symlinks=False)
        return restored, removed

    def rewind_to(self, checkpoint_id: str, keep_untracked: bool = False) -> Dict[str, Any]:
        """Restore a saved checkpoint after a recoverable safety snapshot."""
        target = self.get_checkpoint(checkpoint_id)
        if target is None:
            raise ValueError(f"Checkpoint '{checkpoint_id}' not found.")
        self._validate_checkpoint_backup(target)
        safety = self.create_checkpoint(
            message=f"Auto safety backup before rewind to {checkpoint_id}",
            is_safety=True, evaluate_drift=False)
        if self.is_git_repo() and target.git_commit:
            restored, removed = self._rewind_git_checkpoint(target, safety, keep_untracked)
        else:
            restored, removed = self._rewind_filesystem_checkpoint(checkpoint_id, keep_untracked)
        return {
            "status": "success", "target_checkpoint": target.to_dict(), "safety_checkpoint": safety.to_dict(),
            "restored_count": len(restored), "removed_count": len(removed),
            "restored_files": sorted(restored), "removed_files": sorted(removed),
        }

    def _validate_checkpoint_backup(self, target: CheckpointMetadata) -> None:
        """Validate every checkpoint blob before changing the workspace."""
        directory = self.snapshots_dir / target.id
        if target.git_index_saved:
            if not (directory / "git-index").is_file() and not (directory / "git-index-absent").is_file():
                raise ValueError("Git index backup is missing for checkpoint: %s" % target.id)
            if not (directory / "index-objects").is_dir():
                raise ValueError("Git index objects backup is missing for checkpoint: %s" % target.id)
        if not target.git_worktree_saved:
            return
        if not (directory / "files").is_dir():
            raise ValueError("Working tree backup is missing for checkpoint: %s" % target.id)
        for rel, _mode, kind, _oid in self._tree_entries(target.git_commit):
            source = directory / "files" / rel
            if kind == "blob" and not (source.is_file() or source.is_symlink()):
                raise ValueError("Working tree backup entry is missing: %r" % rel)

    def rewind_step(self, step: int = 1, keep_untracked: bool = False) -> Dict[str, Any]:
        checkpoints = [cp for cp in self._load_index() if not cp.is_safety]
        if not checkpoints:
            raise ValueError("No session checkpoints found to rewind to.")
        idx = min(max(0, step - 1), len(checkpoints) - 1)
        return self.rewind_to(checkpoints[idx].id, keep_untracked=keep_untracked)

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        items = self._load_index()
        found = next((cp for cp in items if cp.id == checkpoint_id), None)
        if not found:
            return False
        if self.is_git_repo() and found.git_ref:
            subprocess.run(["git", "update-ref", "-d", found.git_ref], cwd=str(self.root), check=False)
        snapshot_dir = self.snapshots_dir / checkpoint_id
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        self._save_index([cp for cp in items if cp.id != checkpoint_id])
        return True


def run_checkpoint_cli(args: Any) -> int:
    from .rewind_cli import run_checkpoint_cli as run
    return run(args)


def run_rewind_cli(args: Any) -> int:
    from .rewind_cli import run_rewind_cli as run
    return run(args)

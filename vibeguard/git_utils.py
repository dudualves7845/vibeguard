"""Git integration utilities for VibeGuard.

Handles:
- Staged files retrieval (`git status --porcelain=v1`)
- Diff-aware changed files (staged, unstaged, untracked via single git status call)
- Pre-commit hook installation and uninstallation
- Applying or generating git patch files
"""

import os
from pathlib import Path
import subprocess
from typing import List, Optional, Set, Tuple


class GitIntegration:
    """Manages git hook installation and diff/staged file discovery."""

    HOOK_MARKER = "# VibeGuard managed pre-commit hook v1"
    PRE_COMMIT_HOOK_SCRIPT = """#!/usr/bin/env bash
# VibeGuard managed pre-commit hook v1
# Prevents AI drift, hallucinated packages, ghost API deletions, and secrets from entering the repo.

# Preserve any pre-existing protection before running VibeGuard.
HOOK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$HOOK_DIR/pre-commit.vibeguard-original" ]; then
    "$HOOK_DIR/pre-commit.vibeguard-original" "$@"
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -ne 0 ]; then
        exit "$EXIT_CODE"
    fi
fi

# Run vibeguard check on staged files
if command -v vibeguard >/dev/null 2>&1; then
    VIBEGUARD_BIN=(vibeguard)
elif [ -x "./bin/vibeguard" ]; then
    VIBEGUARD_BIN=("./bin/vibeguard")
elif [ -x "../bin/vibeguard" ]; then
    VIBEGUARD_BIN=("../bin/vibeguard")
else
    # Fallback to python module
    VIBEGUARD_BIN=(python3 -m vibeguard.cli)
fi

echo "🛡️  [VibeGuard Hook] Checking staged files for AI drift & invariants..."
"${VIBEGUARD_BIN[@]}" check --staged

EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
    echo "❌ [VibeGuard Hook] Commit aborted due to drift findings or invariant violations."
    echo "💡 Run 'vibeguard check --staged --prompt' to get AI remediation instructions."
    exit $EXIT_CODE
fi

exit 0
"""

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()

    def is_git_repo(self) -> bool:
        """Checks if the project root is inside a git work tree."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    def get_git_dir(self) -> Optional[Path]:
        """Resolves .git directory path (supports submodules/worktrees)."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=str(self.repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                raw_path = res.stdout.strip()
                git_path = Path(raw_path)
                if not git_path.is_absolute():
                    git_path = (self.repo_root / git_path).resolve()
                return git_path
        except Exception:
            pass
        return None

    def get_git_root(self) -> Path:
        """Discovers repository toplevel directory using git rev-parse --show-toplevel."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(self.repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return Path(res.stdout.strip()).resolve()
        except Exception:
            pass
        return self.repo_root

    def _parse_porcelain_v1_status(self) -> Tuple[List[str], List[str]]:
        """Parses git status --porcelain=v1 in a single invocation.
        
        Returns:
            Tuple of (staged_files, all_changed_files)
        """
        if not self.is_git_repo():
            return [], []
        root = self.get_git_root()

        try:
            res = subprocess.run(
                ["git", "status", "--porcelain=v1", "-z", "-uall"],
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if res.returncode != 0:
                return [], []

            parts = res.stdout.split(b"\0")
            staged_set: Set[str] = set()
            changed_set: Set[str] = set()
            i = 0

            while i < len(parts):
                entry = parts[i]
                if not entry:
                    i += 1
                    continue
                status = entry[:2].decode("utf-8", errors="replace")
                path = os.fsdecode(entry[3:])
                i += 1
                orig_path: Optional[str] = None
                if "R" in status or "C" in status:
                    if i < len(parts):
                        orig_path = os.fsdecode(parts[i])
                        i += 1

                # Staged changes: index column is not ' ' and not '?'
                if status[0] not in (" ", "?", "!"):
                    staged_set.add(path)
                    if orig_path and status[0] in ("R", "C"):
                        staged_set.add(orig_path)

                # All changed changes: any non-clean status
                changed_set.add(path)
                if orig_path:
                    changed_set.add(orig_path)

            return sorted(list(staged_set)), sorted(list(changed_set))
        except Exception:
            return [], []

    def get_staged_files(self) -> List[str]:
        """Returns list of staged relative file paths relative to git root."""
        staged, _ = self._parse_porcelain_v1_status()
        return staged

    def get_changed_files(self) -> List[str]:
        """Returns all changed files (staged, unstaged, and untracked) relative to git root."""
        _, changed = self._parse_porcelain_v1_status()
        return changed

    def get_hooks_dir(self) -> Optional[Path]:
        """Resolves active git hooks directory, respecting core.hooksPath if configured."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--git-path", "hooks"],
                cwd=str(self.repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                raw = res.stdout.strip()
                p = Path(raw)
                return p if p.is_absolute() else (self.repo_root / p).resolve()
        except Exception:
            pass

        git_dir = self.get_git_dir()
        return (git_dir / "hooks") if git_dir else None

    def install_pre_commit_hook(self) -> Path:
        """Installs the VibeGuard pre-commit hook into git hooks directory."""
        hooks_dir = self.get_hooks_dir()
        if not hooks_dir:
            raise RuntimeError(f"Not a git repository: {self.repo_root}")

        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "pre-commit"
        backup = hooks_dir / "pre-commit.vibeguard-original"
        if hook_path.exists() or hook_path.is_symlink():
            if self._is_managed_hook(hook_path):
                hook_path.write_text(self.PRE_COMMIT_HOOK_SCRIPT, encoding="utf-8")
                hook_path.chmod(hook_path.stat().st_mode | 0o755)
                return hook_path
            if backup.exists() or backup.is_symlink():
                raise RuntimeError("Existing hook backup must be reviewed before installing VibeGuard: %s" % backup)
            hook_path.rename(backup)
        hook_path.write_text(self.PRE_COMMIT_HOOK_SCRIPT, encoding="utf-8")
        # Ensure executable permissions (chmod +x)
        current_mode = hook_path.stat().st_mode
        hook_path.chmod(current_mode | 0o755)

        return hook_path

    def uninstall_pre_commit_hook(self) -> bool:
        """Removes the VibeGuard pre-commit hook if present."""
        hooks_dir = self.get_hooks_dir()
        if not hooks_dir:
            return False

        hook_path = hooks_dir / "pre-commit"
        if self._is_managed_hook(hook_path):
            hook_path.unlink()
            backup = hooks_dir / "pre-commit.vibeguard-original"
            if backup.exists() or backup.is_symlink():
                backup.rename(hook_path)
            return True
        return False

    def is_hook_installed(self) -> bool:
        """Checks if the VibeGuard pre-commit hook is currently installed."""
        hooks_dir = self.get_hooks_dir()
        if not hooks_dir:
            return False
        hook_path = hooks_dir / "pre-commit"
        return self._is_managed_hook(hook_path)

    def _is_managed_hook(self, hook_path: Path) -> bool:
        try:
            if hook_path.is_symlink():
                return False
            lines = hook_path.read_text(encoding="utf-8").splitlines()
            return len(lines) > 1 and lines[1] in (
                self.HOOK_MARKER, "# VibeGuard automated pre-commit hook",
            )
        except (OSError, UnicodeError):
            return False

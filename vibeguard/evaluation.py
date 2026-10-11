"""Evaluate each interface against the same project and Git index semantics."""

from pathlib import Path
import subprocess
import tempfile
from typing import List, Optional, Tuple

from .config import GuardConfig
from .drift import DriftEngine, DriftReport
from .git_utils import GitIntegration
from .snapshot import ProjectSnapshot, SnapshotEngine


def project_git_paths(root: Path, paths: List[str], git_root: Path) -> List[str]:
    """Convert Git-root paths to the selected project, excluding other projects."""
    result = []
    for path in paths:
        try:
            # Do not resolve symlinks: deleted paths and index paths are lexical.
            relative = (git_root / path).relative_to(root)
        except ValueError:
            continue
        result.append(relative.as_posix())
    return result


def evaluate_project(root: Path, config: Optional[GuardConfig] = None,
                     staged: bool = False, diff: bool = False,
                     target_files: Optional[List[str]] = None) -> Tuple[DriftReport, Optional[ProjectSnapshot]]:
    """Use an isolated export of the index for staged source and manifests."""
    root = Path(root).resolve()
    # A staged audit must never consult an unstaged policy file, including while
    # opening the canonical baseline. Missing index policy uses product defaults.
    config = config or (GuardConfig(project_name=root.name or "project") if staged else
                        GuardConfig.load_from_file(str(root / ".vibeguard.json")))
    baseline = SnapshotEngine(str(root), config).load_baseline()
    git = GitIntegration(str(root))
    if staged or diff:
        if not git.is_git_repo():
            raise ValueError("Staged/diff checks require a Git repository")
        git_root = git.get_git_root()
        paths = git.get_staged_files() if staged else git.get_changed_files()
        target_files = project_git_paths(root, paths, git_root)
    if staged:
        with tempfile.TemporaryDirectory(prefix="vibeguard-index-") as directory:
            prefix = str(Path(directory)) + "/"
            process = subprocess.run(["git", "checkout-index", "--all", "--prefix=" + prefix],
                                     cwd=str(git_root), capture_output=True, text=True)
            if process.returncode:
                raise ValueError("Cannot read Git index: " + process.stderr.strip())
            staged_root = Path(directory) / root.relative_to(git_root)
            staged_root.mkdir(parents=True, exist_ok=True)
            staged_config = staged_root / ".vibeguard.json"
            config = GuardConfig.load_from_file(str(staged_config))
            report = DriftEngine(str(staged_root), config).evaluate(baseline=baseline, target_files=target_files)
    else:
        report = DriftEngine(str(root), config).evaluate(baseline=baseline, target_files=target_files)
    return report, baseline

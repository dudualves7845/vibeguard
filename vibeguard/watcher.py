"""Real-time file system watcher mode for VibeGuard.

Continuously monitors protected files in the repository during vibe-coding sessions,
detecting drift, hallucinated packages, ghost symbol deletions, and secret leaks
in near-instant background loops.
"""

from pathlib import Path
import time
from typing import Callable, Dict, Optional, Set

from .config import GuardConfig
from .drift import DriftEngine, DriftReport
from .remediation import RemediationGenerator


class FileWatcher:
    """Watches project files for modification, addition, or deletion."""

    def __init__(
        self,
        root_dir: str,
        config: Optional[GuardConfig] = None,
        poll_interval: float = 1.0,
        debounce_seconds: float = 0.5,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.config = config or GuardConfig.load_from_file(str(self.root_dir / ".vibeguard.json"))
        self.poll_interval = poll_interval
        self.debounce_seconds = debounce_seconds
        self.drift_engine = DriftEngine(str(self.root_dir), self.config)
        self._mtimes: Dict[str, float] = {}

    def get_file_state(self) -> Dict[str, float]:
        """Scans protected files and maps relative paths to their last modification time."""
        current_files = self.drift_engine.snapshot_engine.scan_files()
        state: Dict[str, float] = {}
        for path in current_files:
            try:
                rel = str(path.relative_to(self.root_dir))
                state[rel] = path.stat().st_mtime
            except (OSError, ValueError):
                continue
        return state

    def check_changes(self) -> Set[str]:
        """Compares current disk state against tracked state, returning changed file paths."""
        new_state = self.get_file_state()
        changed: Set[str] = set()

        # Files added or modified
        for path, mtime in new_state.items():
            if path not in self._mtimes or self._mtimes[path] != mtime:
                changed.add(path)

        # Files deleted
        for path in self._mtimes:
            if path not in new_state:
                changed.add(path)

        self._mtimes = new_state
        return changed

    def watch_once(self) -> Optional[DriftReport]:
        """Performs a single evaluation pass on changed files (or initial pass)."""
        if not self._mtimes:
            self._mtimes = self.get_file_state()
            # Initial evaluation
            return self.drift_engine.evaluate()

        changed = self.check_changes()
        if not changed:
            return None

        # Re-evaluate drift with target files filter
        return self.drift_engine.evaluate(target_files=sorted(list(changed)))

    def run_loop(
        self,
        on_report: Optional[Callable[[DriftReport, Set[str]], None]] = None,
        max_iterations: Optional[int] = None,
    ) -> None:
        """Runs the continuous watching loop until interrupted or max_iterations reached."""
        self._mtimes = self.get_file_state()
        iterations = 0

        # Run initial audit
        initial_report = self.drift_engine.evaluate()
        if on_report:
            on_report(initial_report, set(self._mtimes.keys()))

        while max_iterations is None or iterations < max_iterations:
            time.sleep(self.poll_interval)
            iterations += 1

            new_state = self.get_file_state()
            changed: Set[str] = set()
            for path, mtime in new_state.items():
                if path not in self._mtimes or self._mtimes[path] != mtime:
                    changed.add(path)
            for path in self._mtimes:
                if path not in new_state:
                    changed.add(path)

            if changed:
                # Debounce fast multi-file saves
                time.sleep(self.debounce_seconds)
                after_debounce = self.get_file_state()
                for path in set(self._mtimes) | set(after_debounce):
                    if self._mtimes.get(path) != after_debounce.get(path):
                        changed.add(path)
                self._mtimes = after_debounce
                report = self.drift_engine.evaluate(target_files=sorted(list(changed)))
                if on_report:
                    on_report(report, changed)

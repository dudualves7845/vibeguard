"""VibeGuard - The AI Vibe Coding Invariant & Drift Engine.

Guards codebases against AI code degradation, hallucinated dependencies,
ghost functions, secret leaks, and architectural contract violations.
"""

__version__ = "1.0.0"
__author__ = "Antigravity AI"

from .ui import VibeGuardService
from .server import run_server, create_server
from .mcp import MCPServer, run_mcp_server
from .badge import BadgeGenerator, BadgeState
from .github_action import post_or_update_pr_comment, render_summary_markdown, run_ci_audit

__all__ = [
    "VibeGuardService",
    "run_server",
    "create_server",
    "MCPServer",
    "run_mcp_server",
    "BadgeGenerator",
    "BadgeState",
    "run_ci_audit",
    "render_summary_markdown",
    "post_or_update_pr_comment",
    "__version__",
]


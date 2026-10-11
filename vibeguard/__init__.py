"""VibeGuard - The AI Vibe Coding Invariant & Drift Engine.

Guards codebases against AI code degradation, hallucinated dependencies,
ghost functions, secret leaks, and architectural contract violations.
"""

__version__ = "1.0.0"
__author__ = "Antigravity AI"

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


def __getattr__(name: str):
    """Lazy load heavy modules only when accessed."""
    if name == "VibeGuardService":
        from .ui import VibeGuardService
        return VibeGuardService
    elif name == "run_server":
        from .server import run_server
        return run_server
    elif name == "create_server":
        from .server import create_server
        return create_server
    elif name == "MCPServer":
        from .mcp import MCPServer
        return MCPServer
    elif name == "run_mcp_server":
        from .mcp import run_mcp_server
        return run_mcp_server
    elif name == "BadgeGenerator":
        from .badge import BadgeGenerator
        return BadgeGenerator
    elif name == "BadgeState":
        from .badge import BadgeState
        return BadgeState
    elif name == "run_ci_audit":
        from .github_action import run_ci_audit
        return run_ci_audit
    elif name == "render_summary_markdown":
        from .github_action import render_summary_markdown
        return render_summary_markdown
    elif name == "post_or_update_pr_comment":
        from .github_action import post_or_update_pr_comment
        return post_or_update_pr_comment
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

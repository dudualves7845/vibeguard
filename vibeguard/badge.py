"""Official VibeGuard Badge Generator.

Generates pixel-perfect, crisp, modern Shields.io-style SVG badges
and markdown/HTML snippets reflecting the health and invariant state of the project.
Zero external dependencies.
"""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from .drift import DriftEngine, DriftReport

# Official Badge States
STATE_VERIFIED = "verified"
STATE_DRIFT = "drift_detected"
STATE_UNANCHORED = "unanchored"
STATE_ERROR = "error"

# Official Status Colors
COLOR_VERIFIED = "#00c853"   # Green
COLOR_DRIFT = "#e53935"      # Red
COLOR_UNANCHORED = "#ffa000" # Amber
COLOR_ERROR = "#6a737d"     # Gray: no trustworthy audit result

# Official Labels and Status Messages
LABEL_PROTECTED = "Protected"
LABEL_VIBEGUARD = "VibeGuard"

MSG_VERIFIED = "No findings"
MSG_DRIFT = "Drift Detected"
MSG_UNANCHORED = "Unanchored"
MSG_ERROR = "Audit Failed"

DEFAULT_LINK_URL = "https://github.com/vibe-guard/vibeguard"
DEFAULT_ALT_TEXT = "VibeGuard Protected"
DEFAULT_FILENAME = "vibeguard-badge.svg"


@dataclass
class BadgeState:
    """Represents evaluated badge state for a project."""
    status: str
    label: str
    message: str
    color: str
    has_baseline: bool
    findings_count: int
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "status": self.status,
            "label": self.label,
            "message": self.message,
            "color": self.color,
            "has_baseline": self.has_baseline,
            "findings_count": self.findings_count,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


def estimate_text_width(text: str) -> int:
    """Estimates the pixel width of text rendered in 11px Verdana/sans-serif."""
    if not text:
        return 0
    width = 0.0
    for ch in str(text):
        if ch in "mwMW%@":
            width += 9.5
        elif ch in "ijl|:;!,.' ()[]/\\":
            width += 4.2
        elif ch in "ft":
            width += 4.8
        elif ch in "ABCDEFGHJKLMNOPQRSTUVXYZ":
            width += 7.8
        elif ch in "0123456789":
            width += 7.0
        elif ch in "abcdeghknopqrsuvxyz":
            width += 6.6
        else:
            width += 7.0
    return max(8, int(round(width)))


def _escape_xml(text: Any) -> str:
    """Escapes special XML characters for safe SVG embedding."""
    s = str(text if text is not None else "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _render_shield_icon(status: str) -> str:
    """Renders crisp vector elements for the shield emblem."""
    shield_outline = (
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" '
        'fill="rgba(255,255,255,0.18)" stroke="#ffffff" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    if status == STATE_VERIFIED:
        inner = (
            '<path d="M9 12l2 2 4-4" fill="none" stroke="#ffffff" stroke-width="2.3" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    elif status == STATE_DRIFT:
        inner = (
            '<path d="M12 8v5M12 16.5h.01" fill="none" stroke="#ffffff" stroke-width="2.3" '
            'stroke-linecap="round"/>'
        )
    else:  # unanchored
        inner = (
            '<path d="M12 9v3.5M12 15.5h.01" fill="none" stroke="#ffffff" stroke-width="2.3" '
            'stroke-linecap="round"/>'
        )

    return f'<g transform="translate(6, 3) scale(0.5833)">{shield_outline}{inner}</g>'


class BadgeGenerator:
    """Zero-dependency generator for crisp SVG badges and documentation snippets."""

    @staticmethod
    def generate_svg(
        label: str,
        message: str,
        color: str,
        status: Optional[str] = None,
        include_icon: bool = True,
    ) -> str:
        """Generates Shields.io-compatible, pixel-perfect SVG markup."""
        esc_label = _escape_xml(label)
        esc_msg = _escape_xml(message)
        aria_label = f"{esc_label}: {esc_msg}"

        label_w = estimate_text_width(label)
        msg_w = estimate_text_width(message)

        if include_icon:
            # 6px margin + 14px icon + 5px gap = 25px offset
            left_width = 25 + label_w + 8
            label_x = 25 + (label_w / 2)
            icon_markup = _render_shield_icon(status or STATE_VERIFIED)
        else:
            left_width = label_w + 16
            label_x = left_width / 2
            icon_markup = ""

        right_width = msg_w + 16
        message_x = left_width + (right_width / 2)
        total_width = left_width + right_width

        # Shields.io coordinate scale convention: x10 for subpixel text placement
        lx10 = int(round(label_x * 10))
        mx10 = int(round(message_x * 10))
        lw10 = int(round(label_w * 10))
        mw10 = int(round(msg_w * 10))

        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20" '
            f'role="img" aria-label="{aria_label}">\n'
            f'  <title>{aria_label}</title>\n'
            f'  <linearGradient id="vg-grad" x2="0" y2="100%">\n'
            f'    <stop offset="0" stop-color="#fff" stop-opacity=".15"/>\n'
            f'    <stop offset="1" stop-opacity=".0"/>\n'
            f'  </linearGradient>\n'
            f'  <clipPath id="vg-clip">\n'
            f'    <rect width="{total_width}" height="20" rx="4" fill="#fff"/>\n'
            f'  </clipPath>\n'
            f'  <g clip-path="url(#vg-clip)">\n'
            f'    <rect width="{left_width}" height="20" fill="#24292e"/>\n'
            f'    <rect x="{left_width}" width="{right_width}" height="20" fill="{color}"/>\n'
            f'    <rect width="{total_width}" height="20" fill="url(#vg-grad)"/>\n'
            f'  </g>\n'
            f'  <g fill="#fff" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif" font-size="110">\n'
            f'    {icon_markup}\n'
            f'    <text aria-hidden="true" x="{lx10}" y="145" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{lw10}">{esc_label}</text>\n'
            f'    <text x="{lx10}" y="135" transform="scale(.1)" fill="#ffffff" font-weight="600" textLength="{lw10}">{esc_label}</text>\n'
            f'    <text aria-hidden="true" x="{mx10}" y="145" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{mw10}">{esc_msg}</text>\n'
            f'    <text x="{mx10}" y="135" transform="scale(.1)" fill="#ffffff" font-weight="600" textLength="{mw10}">{esc_msg}</text>\n'
            f'  </g>\n'
            f'</svg>\n'
        )
        return svg

    @staticmethod
    def generate_badge_for_state(state: BadgeState, include_icon: bool = True) -> str:
        """Generates SVG badge matching the provided BadgeState."""
        return BadgeGenerator.generate_svg(
            label=state.label,
            message=state.message,
            color=state.color,
            status=state.status,
            include_icon=include_icon,
        )

    @staticmethod
    def generate_markdown_snippet(
        image_path: str = DEFAULT_FILENAME,
        link_url: str = DEFAULT_LINK_URL,
        alt_text: str = DEFAULT_ALT_TEXT,
    ) -> str:
        """Generates ready-to-paste Markdown badge snippet."""
        return f"[![{alt_text}]({image_path})]({link_url})"

    @staticmethod
    def generate_html_snippet(
        image_path: str = DEFAULT_FILENAME,
        link_url: str = DEFAULT_LINK_URL,
        alt_text: str = DEFAULT_ALT_TEXT,
    ) -> str:
        """Generates ready-to-paste HTML badge snippet."""
        return f'<a href="{link_url}"><img src="{image_path}" alt="{alt_text}" /></a>'

    @staticmethod
    def save_badge(output_path: Union[str, Path], svg_content: str) -> Path:
        """Saves SVG badge to disk, creating parent directories if needed."""
        target = Path(output_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(svg_content, encoding="utf-8")
        return target


def get_state_from_report(report: Optional[DriftReport], has_baseline: bool) -> BadgeState:
    """Determines BadgeState from a DriftReport and baseline status."""
    if report is None and has_baseline:
        return BadgeState(STATE_ERROR, LABEL_VIBEGUARD, MSG_ERROR, COLOR_ERROR, True, 0,
                          "Audit did not produce a report.")
    if not has_baseline:
        return BadgeState(
            status=STATE_UNANCHORED,
            label=LABEL_VIBEGUARD,
            message=MSG_UNANCHORED,
            color=COLOR_UNANCHORED,
            has_baseline=False,
            findings_count=report.total_findings if report else 0,
        )
    if report and report.total_findings > 0:
        return BadgeState(
            status=STATE_DRIFT,
            label=LABEL_VIBEGUARD,
            message=MSG_DRIFT,
            color=COLOR_DRIFT,
            has_baseline=True,
            findings_count=report.total_findings,
        )
    return BadgeState(
        status=STATE_VERIFIED,
        label=LABEL_PROTECTED,
        message=MSG_VERIFIED,
        color=COLOR_VERIFIED,
        has_baseline=True,
        findings_count=0,
    )


def get_project_state(project_root: Union[str, Path, None] = None) -> BadgeState:
    """Analyzes a project directory and computes its current BadgeState."""
    root = Path(project_root or Path.cwd()).resolve()
    has_baseline = (root / ".vibeguard" / "baseline.json").exists()
    try:
        engine = DriftEngine(str(root))
        baseline = engine.snapshot_engine.load_baseline()
        if baseline is None:
            if has_baseline:
                raise ValueError("Baseline exists but could not be loaded.")
            return get_state_from_report(report=None, has_baseline=False)
        report = engine.evaluate(baseline=baseline)
    except Exception as exc:
        return BadgeState(STATE_ERROR, LABEL_VIBEGUARD, MSG_ERROR, COLOR_ERROR, has_baseline, 0,
                          str(exc))
    return get_state_from_report(report=report, has_baseline=True)


def generate_project_badge_file(
    project_root: Union[str, Path, None] = None,
    output_path: Union[str, Path, None] = None,
    link_url: str = DEFAULT_LINK_URL,
) -> Dict[str, Any]:
    """Generates project badge, saves SVG to disk, and returns metadata and snippets."""
    root = Path(project_root or Path.cwd()).resolve()
    state = get_project_state(root)
    svg = BadgeGenerator.generate_badge_for_state(state)
    if output_path:
        out_p = Path(output_path)
        out = out_p.resolve() if out_p.is_absolute() else (root / out_p).resolve()
    else:
        out = (root / DEFAULT_FILENAME).resolve()
    saved = BadgeGenerator.save_badge(out, svg)

    try:
        rel_img = os.path.relpath(saved, root)
    except ValueError:
        rel_img = str(saved)

    markdown = BadgeGenerator.generate_markdown_snippet(image_path=rel_img, link_url=link_url)
    html = BadgeGenerator.generate_html_snippet(image_path=rel_img, link_url=link_url)

    return {
        **state.to_dict(),
        "svg": svg,
        "output_path": str(saved),
        "relative_path": rel_img,
        "markdown": markdown,
        "html": html,
    }

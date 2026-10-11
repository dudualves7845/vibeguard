"""Command-line presentation for checkpoint and rewind operations."""

from typing import Any

from .rewind import (
    BOLD, CYAN, GREEN, GRAY, RED, YELLOW, CheckpointManager,
    _color, _format_relative_time,
)


def run_checkpoint_cli(args: Any) -> int:
    mgr = CheckpointManager(getattr(args, "project", None))
    msg = " ".join(args.message) if getattr(args, "message", None) else None
    meta = mgr.create_checkpoint(message=msg)
    print(_color(f"📸 [VibeGuard Checkpoint] Created checkpoint {meta.id}", GREEN))
    print(f"   {_color('Message:', GRAY)} {meta.message}")
    print(f"   {_color('Files captured:', GRAY)} {meta.files_count} (staged/modified/untracked)")
    status_icon = "✅" if meta.drift_status == "clean" else ("⚠️" if meta.drift_status == "drift_detected" else "ℹ️")
    print(f"   {_color('Drift state:', GRAY)} {status_icon} {meta.drift_status}")
    print(_color(f"💡 Rewind anytime with: vibeguard rewind --to {meta.id}", CYAN))
    return 0


def run_rewind_cli(args: Any) -> int:
    mgr = CheckpointManager(getattr(args, "project", None))
    if getattr(args, "list", False):
        checkpoints = mgr.list_checkpoints()
        if not checkpoints:
            print(_color("ℹ️  No VibeGuard checkpoints found for this project.", YELLOW))
            print("   Create one using: vibeguard checkpoint [message]")
            return 0
        print(_color(f"\n🛡️  [VibeGuard Rewind] Session Checkpoints ({len(checkpoints)} total):\n", BOLD))
        print(f"  {_color('ID', BOLD):<28} {_color('TIME', BOLD):<10} {_color('HEALTH', BOLD):<12} {_color('MESSAGE', BOLD)}")
        print("  " + "─" * 80)
        for cp in checkpoints:
            time_str = _format_relative_time(cp.timestamp)
            health = "🛡️ Safe" if cp.is_safety else ("✅ Clean" if cp.drift_status == "clean" else ("⚠️ Drift" if cp.drift_status == "drift_detected" else "ℹ️ Unknown"))
            msg_str = f"[{_color('SAFETY', YELLOW)}] {cp.message}" if cp.is_safety else cp.message
            print(f"  {_color(cp.id, CYAN):<28} {time_str:<10} {health:<12} {msg_str}")
        print()
        return 0

    if getattr(args, "diff", None):
        try:
            diff = mgr.get_diff(args.diff)
            print(diff if diff.strip() else _color("ℹ️  No changes between working tree and checkpoint.", GRAY))
            return 0
        except Exception as e:
            print(_color(f"❌ Failed to get diff: {e}", RED))
            return 1

    try:
        to_id = getattr(args, "to", None)
        keep = getattr(args, "keep_untracked", False)
        res = mgr.rewind_to(to_id, keep_untracked=keep) if to_id else mgr.rewind_step(getattr(args, "step", 1) or 1, keep_untracked=keep)
        target = res["target_checkpoint"]
        safety = res["safety_checkpoint"]
        print(_color(f"⏪ [VibeGuard Rewind] Successfully rewound workspace to {target['id']}", GREEN))
        print(f"   {_color('Target Message:', GRAY)} {target['message']}")
        print(f"   {_color('Restored files:', GRAY)} {res['restored_count']} files")
        if res['removed_count'] > 0:
            print(f"   {_color('Removed untracked AI files:', GRAY)} {res['removed_count']} files")
        print(_color(f"🛡️  Safety restore point created: {safety['id']}", CYAN))
        print(_color(f"   (If this was a mistake, undo immediately with: vibeguard rewind --to {safety['id']})", GRAY))
        return 0
    except Exception as e:
        print(_color(f"❌ Failed to rewind: {e}", RED))
        return 1

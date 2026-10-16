"""Conservative restart checkpoint for recent OKX fill history.

A matching anchor establishes overlap only, NOT complete exchange history or
trading readiness. Feed records newest-first (as returned by recent fills).
No guessing missing trades from numeric trade IDs.
"""
from dataclasses import dataclass
from okx_fills import Fill

@dataclass(frozen=True)
class FillCheckpoint:
    anchor_id: str
    anchor_fill: Fill

@dataclass(frozen=True)
class RecoveryResult:
    safe_to_append: bool
    reason: str
    unseen: tuple[Fill, ...]
    next_checkpoint: FillCheckpoint | None
    history_complete: bool = False

def reconcile_fill_window(previous, window):
    if previous is not None and not isinstance(previous, FillCheckpoint):
        raise ValueError("Invalid previous checkpoint")
    if not isinstance(window, (list, tuple)):
        raise ValueError("Invalid fill window")
    seen = set()
    for fill in window:
        if not isinstance(fill, Fill):
            raise ValueError("Invalid fill entry")
        if fill.trade_id in seen:
            return RecoveryResult(False, "duplicate_fill_id", (), previous)
        seen.add(fill.trade_id)
    if not window:
        # An empty recent page cannot establish continuity from an existing anchor.
        if previous is not None:
            return RecoveryResult(False, "checkpoint_not_in_window", (), previous)
        return RecoveryResult(False, "initial_baseline_required", (), None)
    newest = FillCheckpoint(window[0].trade_id, window[0])
    if previous is None:
        return RecoveryResult(False, "initial_baseline_required", (), None)
    matches = [i for i, fill in enumerate(window)
               if fill.trade_id == previous.anchor_id]
    if not matches:
        return RecoveryResult(False, "checkpoint_not_in_window", (), previous)
    anchor_position = matches[0]
    if window[anchor_position] != previous.anchor_fill:
        return RecoveryResult(False, "checkpoint_content_changed", (), previous)
    # All fills newer than the anchored event are candidates, oldest first.
    unseen = tuple(reversed(window[:anchor_position]))
    return RecoveryResult(True, "anchor_overlap_verified", unseen, newest)

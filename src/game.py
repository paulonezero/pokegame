from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def points_for_attempt(attempt_number: int) -> int:
    """Return round points for a correct answer on the given attempt."""
    return {1: 3, 2: 2, 3: 1}.get(int(attempt_number), 0)


def record_guess(
    state: Mapping[str, Any],
    *,
    target_id: int,
    answer_id: int,
) -> dict[str, Any]:
    """Return updated round state after one answer, removing incorrect choices."""
    updated = dict(state)
    if bool(updated.get("completed", False)):
        return updated

    removed = [int(value) for value in updated.get("removed_ids", [])]
    answer_id = int(answer_id)
    target_id = int(target_id)
    if answer_id in removed:
        return updated

    attempt_number = int(updated.get("attempt_count", 0)) + 1
    updated["attempt_count"] = attempt_number
    updated["selected_id"] = answer_id

    if answer_id == target_id:
        updated.update(
            {
                "completed": True,
                "revealed": True,
                "outcome": "correct",
                "points": points_for_attempt(attempt_number),
                "last_wrong_id": None,
            }
        )
    else:
        removed.append(answer_id)
        updated.update(
            {
                "removed_ids": removed,
                "revealed": False,
                "outcome": None,
                "points": 0,
                "last_wrong_id": answer_id,
            }
        )
    return updated


def end_round(state: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    """Finish an active round with no points, normally due to timeout or giving up."""
    if outcome not in {"timed_out", "gave_up"}:
        raise ValueError("outcome must be 'timed_out' or 'gave_up'")
    updated = dict(state)
    if bool(updated.get("completed", False)):
        return updated
    updated.update(
        {
            "completed": True,
            "revealed": True,
            "outcome": outcome,
            "points": 0,
            "selected_id": None,
        }
    )
    return updated

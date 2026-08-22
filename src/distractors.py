from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from .similarity import load_similarity_csv

DIFFICULTY_RANKS: dict[str, tuple[int, int | None]] = {
    "easy": (15, 40),
    "normal": (5, 20),
    "hard": (2, 10),
    "expert": (1, 5),
}


def _target_rows(frame: pd.DataFrame, pokemon_id: int) -> pd.DataFrame:
    direct = frame[frame["target_id"] == pokemon_id].copy()

    reverse_source = frame[frame["similar_id"] == pokemon_id]
    reverse = pd.DataFrame(
        {
            "target_id": reverse_source["similar_id"],
            "target_name": reverse_source["similar_name"],
            "similar_id": reverse_source["target_id"],
            "similar_name": reverse_source["target_name"],
            "overall_score": reverse_source["overall_score"],
            "contour_score": reverse_source["contour_score"],
            "iou_score": reverse_source["iou_score"],
            "radial_score": reverse_source["radial_score"],
            "geometric_score": reverse_source["geometric_score"],
        }
    )
    rows = pd.concat((direct, reverse), ignore_index=True)
    rows = rows[rows["similar_id"] != pokemon_id]
    rows = rows.sort_values(by="overall_score", ascending=False, kind="stable")
    rows = rows.drop_duplicates(subset="similar_id", keep="first").reset_index(drop=True)
    rows["rank"] = np.arange(1, len(rows) + 1)
    return rows


def get_distractors(
    pokemon_id: int,
    difficulty: str = "normal",
    count: int = 3,
    *,
    similarity_data: pd.DataFrame | str | Path | None = None,
    weights: Mapping[str, float] | Iterable[float] | None = None,
    random_state: int | np.random.Generator | None = None,
    as_dataframe: bool = False,
) -> list[dict[str, Any]] | pd.DataFrame:
    """Select unique distractors from a difficulty-specific similarity rank band.

    Easy uses ranks 15–40, normal 5–20, hard 2–10, and expert the
    closest five (or enough closest entries to satisfy a larger ``count``).
    Sampling is random within the band; pass ``random_state`` for repeatability.
    """
    pokemon_id = int(pokemon_id)
    difficulty = difficulty.strip().lower()
    if difficulty not in DIFFICULTY_RANKS:
        raise ValueError(
            f"Unknown difficulty {difficulty!r}; expected one of {tuple(DIFFICULTY_RANKS)}"
        )
    if count < 0:
        raise ValueError("count must be non-negative")

    if similarity_data is None or isinstance(similarity_data, (str, Path)):
        frame = load_similarity_csv(similarity_data, weights=weights)
    else:
        frame = similarity_data.copy()
        if weights is not None:
            from .similarity import reweight_score_rows

            frame = cast(pd.DataFrame, reweight_score_rows(frame, weights))

    ranked = _target_rows(frame, pokemon_id)
    start, configured_end = DIFFICULTY_RANKS[difficulty]
    end = max(configured_end or count, count) if difficulty == "expert" else configured_end
    assert end is not None
    pool = ranked[(ranked["rank"] >= start) & (ranked["rank"] <= end)]

    if count == 0:
        selected = pool.iloc[0:0].copy()
    elif len(pool) < count:
        message = (
            f"Only {len(pool)} unique distractors are available for Pokémon {pokemon_id} "
            f"at {difficulty!r} ranks {start}-{end}; requested {count}"
        )
        raise ValueError(message)
    else:
        rng = (
            random_state
            if isinstance(random_state, np.random.Generator)
            else np.random.default_rng(random_state)
        )
        positions = rng.choice(len(pool), size=count, replace=False)
        selected = pool.iloc[np.sort(positions)].copy().reset_index(drop=True)

    if as_dataframe:
        return selected
    return selected.to_dict(orient="records")


def get_distractor_ids(*args: Any, **kwargs: Any) -> list[int]:
    kwargs["as_dataframe"] = False
    return [int(row["similar_id"]) for row in get_distractors(*args, **kwargs)]

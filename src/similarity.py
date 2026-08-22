from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pandas as pd

from .config import (
    DEFAULT_MAX_IOU_SHIFT,
    DEFAULT_WEIGHTS,
    SCORE_COMPONENTS,
    SIMILARITY_CSV_PATH,
    project_path,
)
from .features import deserialize_features, extract_features
from .image_processing import load_silhouette

SIMILARITY_COLUMNS = (
    "target_id",
    "target_name",
    "similar_id",
    "similar_name",
    "overall_score",
    "contour_score",
    "iou_score",
    "radial_score",
    "geometric_score",
)
_COMPONENT_COLUMNS = {
    "contour": "contour_score",
    "iou": "iou_score",
    "radial": "radial_score",
    "geometric": "geometric_score",
}
_GEOMETRIC_KEYS = (
    "width",
    "height",
    "aspect_ratio",
    "area_ratio",
    "normalized_perimeter",
    "compactness",
    "convex_hull_fill_ratio",
)


def normalize_weights(weights: Mapping[str, float] | Iterable[float] | None = None) -> dict[str, float]:
    """Normalize non-negative UI weights; all-zero input falls back to defaults."""
    if weights is None:
        raw = dict(DEFAULT_WEIGHTS)
    elif isinstance(weights, Mapping):
        provided = cast(Mapping[str, float], weights)
        raw = {
            component: float(
                provided[component]
                if component in provided
                else provided.get(f"{component}_score", 0.0)
            )
            for component in SCORE_COMPONENTS
        }
    else:
        values = tuple(float(value) for value in weights)
        if len(values) != len(SCORE_COMPONENTS):
            raise ValueError(f"Expected {len(SCORE_COMPONENTS)} weights, got {len(values)}")
        raw = dict(zip(SCORE_COMPONENTS, values, strict=True))

    if any(not math.isfinite(value) or value < 0 for value in raw.values()):
        raise ValueError("Similarity weights must be finite and non-negative")
    total = sum(raw.values())
    if total <= 0:
        raw = dict(DEFAULT_WEIGHTS)
        total = sum(raw.values())
    return {component: raw[component] / total for component in SCORE_COMPONENTS}


def contour_similarity(target_contour: np.ndarray, candidate_contour: np.ndarray) -> float:
    distance = float(
        cv2.matchShapes(
            np.asarray(target_contour, dtype=np.float32),
            np.asarray(candidate_contour, dtype=np.float32),
            cv2.CONTOURS_MATCH_I1,
            0.0,
        )
    )
    if not math.isfinite(distance):
        return 0.0
    return float(np.clip(1.0 / (1.0 + max(0.0, distance)), 0.0, 1.0))


def _binary_mask(mask_or_path: np.ndarray | str | Path) -> np.ndarray:
    if isinstance(mask_or_path, (str, Path)):
        return load_silhouette(mask_or_path)
    mask = (np.asarray(mask_or_path) > 0).astype(np.uint8)
    if mask.ndim != 2:
        raise ValueError("silhouette masks must be two-dimensional")
    return mask


def _shift_regions(
    shape: tuple[int, int], dx: int, dy: int
) -> tuple[slice, slice, slice, slice]:
    height, width = shape
    destination_x = slice(max(0, dx), min(width, width + dx))
    destination_y = slice(max(0, dy), min(height, height + dy))
    source_x = slice(max(0, -dx), min(width, width - dx))
    source_y = slice(max(0, -dy), min(height, height - dy))
    return destination_y, destination_x, source_y, source_x


def shift_mask(mask: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Translate a mask without wraparound; positive shifts move right/down."""
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    shifted = np.zeros_like(binary)
    destination_y, destination_x, source_y, source_x = _shift_regions(binary.shape, dx, dy)
    shifted[destination_y, destination_x] = binary[source_y, source_x]
    return shifted


def best_shifted_iou(
    target_mask: np.ndarray,
    candidate_mask: np.ndarray,
    max_shift: int = DEFAULT_MAX_IOU_SHIFT,
) -> tuple[float, tuple[int, int], np.ndarray, np.ndarray]:
    """Return best IoU, ``(dx, dy)``, aligned candidate, and overlap mask."""
    target = (np.asarray(target_mask) > 0).astype(np.uint8)
    candidate = (np.asarray(candidate_mask) > 0).astype(np.uint8)
    if target.shape != candidate.shape or target.ndim != 2:
        raise ValueError("target and candidate masks must be same-sized 2D arrays")
    if max_shift < 0:
        raise ValueError("max_shift must be non-negative")

    target_count = int(np.count_nonzero(target))
    best_score = -1.0
    best_shift = (0, 0)
    best_tie = (0, 0, 0)
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            destination_y, destination_x, source_y, source_x = _shift_regions(target.shape, dx, dy)
            candidate_region = candidate[source_y, source_x]
            intersection = int(
                np.count_nonzero(target[destination_y, destination_x] & candidate_region)
            )
            candidate_count = int(np.count_nonzero(candidate_region))
            union = target_count + candidate_count - intersection
            score = intersection / union if union else 1.0
            tie = (abs(dx) + abs(dy), abs(dy), abs(dx))
            if score > best_score + 1e-12 or (
                abs(score - best_score) <= 1e-12 and tie < best_tie
            ):
                best_score = score
                best_shift = (dx, dy)
                best_tie = tie

    aligned = shift_mask(candidate, *best_shift)
    overlap = (target & aligned).astype(np.uint8)
    return float(np.clip(best_score, 0.0, 1.0)), best_shift, aligned, overlap


def radial_cosine_similarity(target_profile: np.ndarray, candidate_profile: np.ndarray) -> float:
    target = np.asarray(target_profile, dtype=np.float64).ravel()
    candidate = np.asarray(candidate_profile, dtype=np.float64).ravel()
    if target.shape != candidate.shape:
        raise ValueError("radial profiles must have the same number of samples")
    target_norm = float(np.linalg.norm(target))
    candidate_norm = float(np.linalg.norm(candidate))
    if target_norm == 0 and candidate_norm == 0:
        return 1.0
    if target_norm == 0 or candidate_norm == 0:
        return 0.0
    cosine = float(np.dot(target, candidate) / (target_norm * candidate_norm))
    return float(np.clip(cosine, 0.0, 1.0))


def _relative_similarity(first: float, second: float) -> float:
    scale = max(abs(first), abs(second))
    if scale <= 1e-12:
        return 1.0
    return float(np.clip(1.0 - abs(first - second) / scale, 0.0, 1.0))


def geometric_similarity(
    target_features: Mapping[str, Any], candidate_features: Mapping[str, Any]
) -> float:
    scores = [
        _relative_similarity(float(target_features[key]), float(candidate_features[key]))
        for key in _GEOMETRIC_KEYS
    ]
    return float(np.mean(scores))


def _orientation_score(
    target_mask: np.ndarray,
    candidate_mask: np.ndarray,
    target_features: Mapping[str, Any],
    candidate_features: Mapping[str, Any],
    weights: Mapping[str, float],
    max_shift: int,
) -> dict[str, Any]:
    iou, shift, aligned, overlap = best_shifted_iou(target_mask, candidate_mask, max_shift)
    components = {
        "contour": contour_similarity(target_features["contour"], candidate_features["contour"]),
        "iou": iou,
        "radial": radial_cosine_similarity(
            target_features["radial_profile"], candidate_features["radial_profile"]
        ),
        "geometric": geometric_similarity(target_features, candidate_features),
    }
    overall = sum(weights[name] * components[name] for name in SCORE_COMPONENTS)
    return {
        "overall_score": float(np.clip(overall, 0.0, 1.0)),
        "contour_score": components["contour"],
        "iou_score": components["iou"],
        "radial_score": components["radial"],
        "geometric_score": components["geometric"],
        "shift": shift,
        "aligned_mask": aligned,
        "overlap": overlap,
    }


def compare_silhouettes(
    target_mask: np.ndarray | str | Path,
    candidate_mask: np.ndarray | str | Path,
    *,
    target_features: Mapping[str, Any] | None = None,
    candidate_features: Mapping[str, Any] | None = None,
    candidate_flipped_features: Mapping[str, Any] | None = None,
    weights: Mapping[str, float] | Iterable[float] | None = None,
    max_shift: int = DEFAULT_MAX_IOU_SHIFT,
    return_debug: bool = False,
) -> dict[str, Any]:
    """Score normal and mirrored candidates, selecting one orientation coherently.

    Each orientation gets all four components and its own best IoU translation.
    The orientation with the highest weighted overall score wins; ties prefer the
    original. This avoids mixing the contour/radial score from one orientation
    with the aligned mask from another.
    """
    target = _binary_mask(target_mask)
    candidate = _binary_mask(candidate_mask)
    if target.shape != candidate.shape:
        raise ValueError("target and candidate silhouettes must use the same canvas size")

    normalized_weights = normalize_weights(weights)
    target_data = (
        extract_features(target)
        if target_features is None
        else deserialize_features(target_features)
    )
    normal_data = (
        extract_features(candidate)
        if candidate_features is None
        else deserialize_features(candidate_features)
    )
    flipped = np.ascontiguousarray(np.fliplr(candidate))
    flipped_data = (
        extract_features(flipped, int(target_data.get("radial_samples", 360)))
        if candidate_flipped_features is None
        else deserialize_features(candidate_flipped_features)
    )

    normal = _orientation_score(
        target, candidate, target_data, normal_data, normalized_weights, max_shift
    )
    mirrored = _orientation_score(
        target, flipped, target_data, flipped_data, normalized_weights, max_shift
    )
    orientation = "flipped" if mirrored["overall_score"] > normal["overall_score"] else "normal"
    selected = mirrored if orientation == "flipped" else normal

    result = {
        "overall_score": selected["overall_score"],
        "contour_score": selected["contour_score"],
        "iou_score": selected["iou_score"],
        "radial_score": selected["radial_score"],
        "geometric_score": selected["geometric_score"],
        "orientation": orientation,
    }
    if return_debug:
        result["debug"] = {
            "orientation": orientation,
            "shift": selected["shift"],
            "aligned_mask": selected["aligned_mask"],
            "overlap": selected["overlap"],
            "overlap_mask": selected["overlap"],
            "overlap_pixels": int(np.count_nonzero(selected["overlap"])),
            "weights": normalized_weights,
            "normal_overall_score": normal["overall_score"],
            "flipped_overall_score": mirrored["overall_score"],
        }
    return result


def score_pair(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return compare_silhouettes(*args, **kwargs)


def reweight_score_rows(
    rows: pd.DataFrame | Mapping[str, Any] | Iterable[Mapping[str, Any]],
    weights: Mapping[str, float] | Iterable[float] | None = None,
) -> pd.DataFrame | dict[str, Any] | list[dict[str, Any]]:
    """Recompute overall scores from existing component columns."""
    normalized = normalize_weights(weights)
    single = isinstance(rows, Mapping)
    is_frame = isinstance(rows, pd.DataFrame)
    if is_frame:
        frame = rows.copy()
    elif single:
        frame = pd.DataFrame([dict(rows)])
    else:
        frame = pd.DataFrame(list(rows))

    missing = [column for column in _COMPONENT_COLUMNS.values() if column not in frame.columns]
    if missing:
        raise ValueError(f"Score rows are missing component columns: {missing}")
    overall = np.zeros(len(frame), dtype=np.float64)
    for component, column in _COMPONENT_COLUMNS.items():
        values = np.asarray(
            pd.to_numeric(frame[column], errors="raise"), dtype=np.float64
        )
        overall += normalized[component] * np.clip(values, 0.0, 1.0)
    frame["overall_score"] = np.clip(overall, 0.0, 1.0)

    if is_frame:
        return frame
    records = frame.to_dict(orient="records")
    return records[0] if single else records


def load_similarity_csv(
    path: str | Path | None = None,
    *,
    weights: Mapping[str, float] | Iterable[float] | None = None,
) -> pd.DataFrame:
    """Load and validate the standard pairwise similarity CSV schema."""
    input_path = project_path(path) if path is not None else SIMILARITY_CSV_PATH.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Similarity CSV not found: {input_path}")
    frame: pd.DataFrame = pd.read_csv(input_path)
    missing = [column for column in SIMILARITY_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Similarity CSV is missing required columns: {missing}")

    frame = frame.copy()
    for column in ("target_id", "similar_id"):
        frame[column] = pd.Series(
            pd.to_numeric(frame[column], errors="raise"),
            index=frame.index,
            dtype="int64",
        )
    for column in (
        "overall_score",
        "contour_score",
        "iou_score",
        "radial_score",
        "geometric_score",
    ):
        frame[column] = np.clip(
            np.asarray(pd.to_numeric(frame[column], errors="raise"), dtype=np.float64),
            0.0,
            1.0,
        )
    frame["target_name"] = frame["target_name"].astype(str).str.strip()
    frame["similar_name"] = frame["similar_name"].astype(str).str.strip()
    if weights is not None:
        frame = cast(pd.DataFrame, reweight_score_rows(frame, weights))
    return frame


reweight_scores = reweight_score_rows

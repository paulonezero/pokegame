from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import DEFAULT_RADIAL_SAMPLES, project_path
from .image_processing import load_silhouette

FeatureMap = dict[str, Any]


def _binary_mask(mask_or_path: np.ndarray | str | Path) -> np.ndarray:
    if isinstance(mask_or_path, (str, Path)):
        mask = load_silhouette(mask_or_path)
    else:
        mask = (np.asarray(mask_or_path) > 0).astype(np.uint8)
    if mask.ndim != 2:
        raise ValueError("silhouette mask must be two-dimensional")
    if not np.any(mask):
        raise ValueError("silhouette mask contains no foreground")
    return mask


def primary_external_contour(mask: np.ndarray) -> np.ndarray:
    """Return the largest external contour as OpenCV ``(N, 1, 2)`` points."""
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise ValueError("silhouette mask contains no contour")
    return max(contours, key=cv2.contourArea)


def robust_interior_center(mask: np.ndarray) -> tuple[float, float]:
    """Choose the deepest interior point using the Euclidean distance transform."""
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    _, maximum, _, location = cv2.minMaxLoc(distance)
    if maximum > 0:
        return float(location[0]), float(location[1])

    moments = cv2.moments(binary, binaryImage=True)
    if moments["m00"] == 0:
        raise ValueError("silhouette mask contains no foreground")
    return moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]


def radial_profile(
    contour: np.ndarray,
    center: tuple[float, float],
    samples: int = DEFAULT_RADIAL_SAMPLES,
) -> np.ndarray:
    """Sample the outer contour radius by angle and normalize its maximum to one."""
    if samples < 8:
        raise ValueError("radial profile requires at least 8 samples")
    points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    if not len(points):
        raise ValueError("contour contains no points")

    offsets = points - np.asarray(center, dtype=np.float64)
    angles = np.mod(np.arctan2(offsets[:, 1], offsets[:, 0]), 2.0 * math.pi)
    radii = np.linalg.norm(offsets, axis=1)
    bins = np.floor(angles * samples / (2.0 * math.pi)).astype(int) % samples

    profile = np.full(samples, np.nan, dtype=np.float64)
    for index, radius in zip(bins, radii, strict=True):
        if np.isnan(profile[index]) or radius > profile[index]:
            profile[index] = radius

    known = np.flatnonzero(~np.isnan(profile))
    if not len(known):
        return np.zeros(samples, dtype=np.float64)
    if len(known) == 1:
        profile.fill(profile[known[0]])
    elif len(known) < samples:
        x = np.arange(samples)
        extended_x = np.concatenate((known - samples, known, known + samples))
        extended_values = np.tile(profile[known], 3)
        profile = np.interp(x, extended_x, extended_values)

    maximum = float(profile.max(initial=0.0))
    if maximum > 0:
        profile /= maximum
    return profile


def extract_features(
    mask_or_path: np.ndarray | str | Path,
    radial_samples: int = DEFAULT_RADIAL_SAMPLES,
) -> FeatureMap:
    """Extract contour, radial, and normalized geometric silhouette features."""
    mask = _binary_mask(mask_or_path)
    canvas_height, canvas_width = mask.shape
    contour = primary_external_contour(mask)
    center = robust_interior_center(mask)
    profile = radial_profile(contour, center, radial_samples)

    _, _, bbox_width, bbox_height = cv2.boundingRect(contour)
    area = float(np.count_nonzero(mask))
    contour_area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))
    hull = cv2.convexHull(contour)
    hull_area = float(cv2.contourArea(hull))

    return {
        "contour": contour.astype(np.int32),
        "radial_profile": profile,
        "center": center,
        "width": bbox_width / canvas_width,
        "height": bbox_height / canvas_height,
        "aspect_ratio": bbox_width / bbox_height if bbox_height else 0.0,
        "area_ratio": area / mask.size,
        "normalized_perimeter": perimeter / (2.0 * (canvas_width + canvas_height)),
        "compactness": (4.0 * math.pi * contour_area / (perimeter * perimeter)) if perimeter else 0.0,
        "convex_hull_fill_ratio": (contour_area / hull_area) if hull_area else 0.0,
        "foreground_area": area,
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "radial_samples": radial_samples,
    }


def serialize_features(features: Mapping[str, Any]) -> dict[str, Any]:
    """Convert feature arrays and NumPy scalars into JSON-safe Python values."""
    result: dict[str, Any] = {}
    for key, value in features.items():
        if isinstance(value, np.ndarray):
            array = value.reshape(-1, 2) if key == "contour" else value
            result[key] = array.tolist()
        elif isinstance(value, np.generic):
            result[key] = value.item()
        elif key == "center":
            result[key] = [float(value[0]), float(value[1])]
        else:
            result[key] = value
    return result


def deserialize_features(features: Mapping[str, Any]) -> FeatureMap:
    """Restore JSON-safe feature data to the array shapes used by OpenCV."""
    result = dict(features)
    if "contour" in result:
        result["contour"] = np.asarray(result["contour"], dtype=np.float32).reshape(-1, 1, 2)
    if "radial_profile" in result:
        result["radial_profile"] = np.asarray(result["radial_profile"], dtype=np.float64)
    if "center" in result:
        result["center"] = tuple(float(value) for value in result["center"])
    return result


def save_features(features: Mapping[str, Any], path: str | Path) -> Path:
    output_path = project_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(serialize_features(features), handle, indent=2, ensure_ascii=False)
    return output_path


def load_features(path: str | Path) -> FeatureMap:
    input_path = project_path(path)
    with input_path.open("r", encoding="utf-8") as handle:
        return deserialize_features(json.load(handle))


features_to_dict = serialize_features
features_from_dict = deserialize_features

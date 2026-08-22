from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .config import (
    DEFAULT_ALPHA_THRESHOLD,
    DEFAULT_CANVAS_SIZE,
    DEFAULT_PADDING,
    SILHOUETTES_DIR,
    project_path,
)

ImageInput = str | Path | Image.Image | np.ndarray


def _alpha_channel(image: ImageInput) -> np.ndarray:
    if isinstance(image, (str, Path)):
        path = project_path(image)
        with Image.open(path) as opened:
            has_transparency = "A" in opened.getbands() or "transparency" in opened.info
            if not has_transparency:
                raise ValueError(f"Artwork must contain transparency/alpha: {path}")
            return np.asarray(opened.convert("RGBA"), dtype=np.uint8)[..., 3]

    if isinstance(image, Image.Image):
        if "A" not in image.getbands() and "transparency" not in image.info:
            raise ValueError("Artwork must contain transparency/alpha")
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)[..., 3]

    array = np.asarray(image)
    if array.ndim == 2:
        return array
    if array.ndim != 3 or array.shape[2] != 4:
        raise ValueError("Artwork arrays must be an alpha mask or have four channels")
    return array[..., 3]


def alpha_to_binary_mask(
    image: ImageInput,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
) -> np.ndarray:
    """Build a ``uint8`` 0/1 foreground mask strictly from artwork alpha."""
    if not 0 <= alpha_threshold <= 255:
        raise ValueError("alpha_threshold must be between 0 and 255")
    alpha = _alpha_channel(image)
    return (alpha > alpha_threshold).astype(np.uint8)


def crop_foreground(mask: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Crop a mask to its foreground bbox, returned as ``(left, top, right, bottom)``."""
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    if binary.ndim != 2:
        raise ValueError("mask must be a two-dimensional array")
    ys, xs = np.nonzero(binary)
    if not len(xs):
        raise ValueError("Artwork alpha contains no foreground pixels")
    left, right = int(xs.min()), int(xs.max()) + 1
    top, bottom = int(ys.min()), int(ys.max()) + 1
    return binary[top:bottom, left:right], (left, top, right, bottom)


def center_mask_on_canvas(
    cropped_mask: np.ndarray,
    canvas_size: int = DEFAULT_CANVAS_SIZE,
    padding: int = DEFAULT_PADDING,
) -> np.ndarray:
    """Aspect-fit and center a cropped binary mask on a square canvas."""
    if canvas_size <= 0:
        raise ValueError("canvas_size must be positive")
    if padding < 0 or padding * 2 >= canvas_size:
        raise ValueError("padding must be non-negative and leave drawable canvas space")

    binary = (np.asarray(cropped_mask) > 0).astype(np.uint8)
    if binary.ndim != 2 or not np.any(binary):
        raise ValueError("cropped_mask must be a non-empty two-dimensional foreground mask")

    height, width = binary.shape
    available = canvas_size - 2 * padding
    scale = min(available / width, available / height)
    resized_width = max(1, min(available, int(round(width * scale))))
    resized_height = max(1, min(available, int(round(height * scale))))
    resized = cv2.resize(
        binary,
        (resized_width, resized_height),
        interpolation=cv2.INTER_NEAREST,
    )
    resized = (resized > 0).astype(np.uint8)

    canvas = np.zeros((canvas_size, canvas_size), dtype=np.uint8)
    left = (canvas_size - resized_width) // 2
    top = (canvas_size - resized_height) // 2
    canvas[top : top + resized_height, left : left + resized_width] = resized
    return canvas


def process_silhouette(
    image: ImageInput,
    canvas_size: int = DEFAULT_CANVAS_SIZE,
    padding: int = DEFAULT_PADDING,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    *,
    return_debug: bool = False,
) -> np.ndarray | tuple[np.ndarray, dict[str, Any]]:
    """Convert transparent artwork into a centered, aspect-preserving binary mask."""
    alpha_mask = alpha_to_binary_mask(image, alpha_threshold)
    cropped, bbox = crop_foreground(alpha_mask)
    silhouette = center_mask_on_canvas(cropped, canvas_size, padding)
    if not return_debug:
        return silhouette
    return silhouette, {
        "source_bbox": bbox,
        "source_size": tuple(int(value) for value in alpha_mask.shape[::-1]),
        "cropped_size": tuple(int(value) for value in cropped.shape[::-1]),
        "canvas_size": canvas_size,
        "padding": padding,
    }


def save_silhouette(mask: np.ndarray, path: str | Path) -> Path:
    """Save a binary mask as a display-friendly 8-bit PNG."""
    output_path = project_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    if binary.ndim != 2:
        raise ValueError("mask must be a two-dimensional array")
    Image.fromarray(binary * 255).save(output_path)
    return output_path


def load_silhouette(path: str | Path) -> np.ndarray:
    """Load any grayscale/RGB silhouette image as a binary 0/1 mask."""
    input_path = project_path(path)
    with Image.open(input_path) as image:
        gray = np.asarray(image.convert("L"), dtype=np.uint8)
    return (gray > 0).astype(np.uint8)


def process_and_save_silhouette(
    image: ImageInput,
    output_path: str | Path | None = None,
    *,
    pokemon_id: int | None = None,
    canvas_size: int = DEFAULT_CANVAS_SIZE,
    padding: int = DEFAULT_PADDING,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
) -> tuple[np.ndarray, Path]:
    """Process artwork and save it, returning both the mask and resolved path."""
    mask = process_silhouette(
        image,
        canvas_size,
        padding,
        alpha_threshold,
        return_debug=False,
    )
    assert isinstance(mask, np.ndarray)
    if output_path is None:
        if pokemon_id is not None:
            output_path = SILHOUETTES_DIR / f"{int(pokemon_id):03d}.png"
        elif isinstance(image, (str, Path)):
            output_path = SILHOUETTES_DIR / f"{Path(image).stem}.png"
        else:
            raise ValueError("output_path or pokemon_id is required for in-memory artwork")
    saved_path = save_silhouette(mask, output_path)
    return mask, saved_path


# Short aliases useful in data preparation scripts.
create_silhouette = process_silhouette
process_and_save = process_and_save_silhouette

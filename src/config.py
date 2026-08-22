from __future__ import annotations

from pathlib import Path
from typing import Final

# Resolve from this file so callers are independent of their current working directory.
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
SRC_DIR: Final[Path] = PROJECT_ROOT / "src"
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
POKEMON_DIR: Final[Path] = DATA_DIR / "pokemon"
ARTWORK_DIR: Final[Path] = POKEMON_DIR / "artwork"
SILHOUETTES_DIR: Final[Path] = DATA_DIR / "silhouettes"
FEATURES_DIR: Final[Path] = DATA_DIR / "features"
SIMILARITY_DIR: Final[Path] = DATA_DIR / "similarity"
POKEMON_METADATA_PATH: Final[Path] = POKEMON_DIR / "metadata.json"
SIMILARITY_CSV_PATH: Final[Path] = SIMILARITY_DIR / "similarity.csv"

GEN_I_IDS: Final[tuple[int, ...]] = tuple(range(1, 152))
DEFAULT_CANVAS_SIZE: Final[int] = 256
DEFAULT_PADDING: Final[int] = 16
DEFAULT_ALPHA_THRESHOLD: Final[int] = 0
DEFAULT_RADIAL_SAMPLES: Final[int] = 360
DEFAULT_MAX_IOU_SHIFT: Final[int] = 5

SCORE_COMPONENTS: Final[tuple[str, ...]] = (
    "contour",
    "iou",
    "radial",
    "geometric",
)
# This is the single editable source for application scoring defaults.
DEFAULT_WEIGHTS: Final[dict[str, float]] = {
    "contour": 0.4,
    "iou": 0.3,
    "radial": 0.2,
    "geometric": 0.1,
}


def project_path(path: str | Path, *parts: str | Path) -> Path:
    """Return an absolute path rooted at the project for relative inputs."""
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    if parts:
        resolved = resolved.joinpath(*(Path(part) for part in parts))
    return resolved.resolve()

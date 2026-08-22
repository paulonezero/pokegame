from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from PIL import Image
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("POKEGAME_DATA_DIR", PROJECT_ROOT / "data")).expanduser().resolve()
METADATA_PATH = DATA_DIR / "pokemon" / "metadata.json"
MASKS_DIR = DATA_DIR / "silhouettes"
SIMILARITY_COLUMNS = {
    "target_id",
    "target_name",
    "similar_id",
    "similar_name",
    "overall_score",
    "contour_score",
    "iou_score",
    "radial_score",
    "geometric_score",
}
SCORE_COLUMNS = ["contour_score", "iou_score", "radial_score", "geometric_score"]


class SetupError(RuntimeError):
    """Raised when required generated game data is absent or malformed."""


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _first_value(mapping: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        digits = "".join(character for character in str(value) if character.isdigit())
        return int(digits) if digits else None


def _metadata_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("pokemon", "items", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _find_art_value(item: dict[str, Any]) -> str | None:
    value = _first_value(
        item,
        (
            "art_path",
            "image_path",
            "artwork_path",
            "sprite_path",
            "official_artwork",
            "artwork",
            "image",
            "art",
            "sprite",
        ),
    )
    if isinstance(value, dict):
        value = _first_value(value, ("local_path", "path", "file", "front_default"))
    return str(value) if isinstance(value, (str, Path)) and str(value).strip() else None


def resolve_local_path(value: str | None, metadata_path: Path = METADATA_PATH) -> Path | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    possibilities = [candidate] if candidate.is_absolute() else [
        PROJECT_ROOT / candidate,
        metadata_path.parent / candidate,
        metadata_path.parent / candidate.name,
    ]
    return next((path.resolve() for path in possibilities if path.is_file()), None)


@st.cache_data(show_spinner=False)
def load_metadata(path_string: str = str(METADATA_PATH)) -> list[dict[str, Any]]:
    path = Path(path_string)
    if not path.is_file():
        raise SetupError(f"Game data was not found at `{_display_path(path)}`.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"Could not read `{_display_path(path)}`: {exc}") from exc

    records: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for item in _metadata_entries(payload):
        pokemon_id = _as_int(_first_value(item, ("id", "pokemon_id", "pokedex_id", "number")))
        name = _first_value(item, ("name", "pokemon_name", "display_name", "species"))
        if pokemon_id is None or not name or pokemon_id in seen_ids:
            continue
        generation = _as_int(_first_value(item, ("generation", "gen")))
        art_path = resolve_local_path(_find_art_value(item), path)
        records.append(
            {
                "id": pokemon_id,
                "name": str(name).replace("-", " ").title(),
                "generation": generation,
                "art_path": str(art_path) if art_path else None,
            }
        )
        seen_ids.add(pokemon_id)

    if not records:
        raise SetupError("The packaged metadata contains no usable Pokémon records.")
    return sorted(records, key=lambda record: record["id"])


def playable_pokemon(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every valid National Pokédex record available to the game."""
    return [record for record in records if int(record["id"]) > 0]


def generation_one(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return Generation I records for callers that explicitly need that subset."""
    explicit = [record for record in records if record.get("generation") == 1]
    return explicit or [record for record in records if 1 <= record["id"] <= 151]


def mask_path(pokemon_id: int) -> Path:
    padded = MASKS_DIR / f"{pokemon_id:03d}.png"
    legacy = MASKS_DIR / f"{pokemon_id}.png"
    return padded if padded.is_file() or not legacy.is_file() else legacy


def find_similarity_csv() -> Path:
    preferred = (
        DATA_DIR / "similarity" / "similarity.csv",
        DATA_DIR / "similarity" / "similarity_scores.csv",
        DATA_DIR / "similarity" / "pokemon_similarity.csv",
        DATA_DIR / "similarity" / "scores.csv",
        DATA_DIR / "similarity.csv",
    )
    candidates = list(preferred)
    similarity_dir = DATA_DIR / "similarity"
    if similarity_dir.is_dir():
        candidates.extend(sorted(similarity_dir.glob("*.csv")))

    checked: set[Path] = set()
    for path in candidates:
        if path in checked or not path.is_file():
            continue
        checked.add(path)
        try:
            columns = set(pd.read_csv(path, nrows=0).columns)
        except (OSError, pd.errors.ParserError, UnicodeDecodeError):
            continue
        if SIMILARITY_COLUMNS.issubset(columns):
            return path
    raise SetupError("The packaged similarity index is missing or invalid.")


@st.cache_data(show_spinner=False)
def load_similarity(path_string: str) -> pd.DataFrame:
    path = Path(path_string)
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise SetupError(f"Could not read similarity data: {exc}") from exc

    missing = SIMILARITY_COLUMNS.difference(frame.columns)
    if missing:
        raise SetupError("Similarity data is missing required columns: " + ", ".join(sorted(missing)))
    frame = frame.copy()
    frame["target_id"] = pd.to_numeric(frame["target_id"], errors="coerce")
    frame["similar_id"] = pd.to_numeric(frame["similar_id"], errors="coerce")
    for column in ["overall_score", *SCORE_COLUMNS]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["target_id", "similar_id", *SCORE_COLUMNS])
    frame["target_id"] = frame["target_id"].astype(int)
    frame["similar_id"] = frame["similar_id"].astype(int)
    return frame


def rows_for_target(frame: pd.DataFrame, target_id: int) -> pd.DataFrame:
    direct = frame.loc[frame["target_id"] == target_id].copy()
    if direct.empty:
        reverse = frame.loc[frame["similar_id"] == target_id].copy()
        if not reverse.empty:
            direct = reverse.rename(
                columns={
                    "target_id": "similar_id",
                    "target_name": "similar_name",
                    "similar_id": "target_id",
                    "similar_name": "target_name",
                }
            )
    return direct.loc[direct["similar_id"] != target_id].drop_duplicates("similar_id")


@st.cache_data(show_spinner=False)
def load_art(path_string: str) -> Image.Image:
    with Image.open(path_string) as image:
        return image.convert("RGBA")


@st.cache_data(show_spinner=False)
def load_mask(path_string: str) -> np.ndarray:
    with Image.open(path_string) as image:
        gray = np.asarray(image.convert("L"), dtype=np.uint8)
    mask = gray > 0
    if not mask.any():
        raise SetupError("A packaged silhouette contains no foreground pixels.")
    return mask


def render_silhouette(
    mask: np.ndarray,
    background: tuple[int, int, int] = (238, 240, 243),
) -> Image.Image:
    canvas = np.empty((*mask.shape, 3), dtype=np.uint8)
    canvas[:] = background
    canvas[mask] = (0, 0, 0)
    return Image.fromarray(canvas, mode="RGB")


def load_rendered_silhouette(pokemon_id: int) -> Image.Image:
    path = mask_path(pokemon_id)
    if not path.is_file():
        raise SetupError("A required packaged silhouette is missing.")
    return render_silhouette(load_mask(str(path)))

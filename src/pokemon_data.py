from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from .config import (
    ARTWORK_DIR,
    FEATURES_DIR,
    GEN_I_IDS,
    POKEMON_METADATA_PATH,
    SILHOUETTES_DIR,
    project_path,
)

_ID_COLUMNS = ("id", "pokemon_id", "pokedex_number", "dex_number", "number", "#")
_NAME_COLUMNS = ("name", "pokemon_name", "pokemon")


def _normalise_column_name(value: object) -> str:
    return re.sub(r"[^a-z0-9#]+", "_", str(value).strip().lower()).strip("_")


def _metadata_path(path: str | Path | None) -> Path:
    if path is not None:
        return project_path(path)
    candidates = (
        POKEMON_METADATA_PATH,
        POKEMON_METADATA_PATH.with_suffix(".csv"),
        ARTWORK_DIR.parent / "pokemon.csv",
        ARTWORK_DIR.parent / "pokemon_data.csv",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), POKEMON_METADATA_PATH)


def _json_records(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Pokémon metadata JSON at {path}: {exc}") from exc

    if isinstance(payload, dict):
        for key in ("pokemon", "items", "results", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"Metadata JSON at {path} must contain a list of objects")
    return payload


def load_pokemon_metadata(
    path: str | Path | None = None,
    ids: Iterable[int] | None = GEN_I_IDS,
) -> pd.DataFrame:
    """Load JSON or CSV metadata with canonical ``id`` and ``name`` columns.

    The downloader writes JSON, while CSV remains supported for quick analysis and
    future data imports. By default records are limited to IDs 1–151; pass
    ``ids=None`` to load every available Pokémon.
    """
    metadata_path = _metadata_path(path)
    if not metadata_path.is_file():
        raise FileNotFoundError(
            f"Pokémon metadata not found: {metadata_path}. Run scripts/download_images.py first."
        )

    if metadata_path.suffix.lower() == ".json":
        frame = pd.DataFrame(_json_records(metadata_path))
    else:
        frame = pd.read_csv(metadata_path)
    frame = frame.rename(
        columns={str(column): _normalise_column_name(column) for column in frame.columns}
    )

    id_column = next((column for column in _ID_COLUMNS if column in frame.columns), None)
    name_column = next((column for column in _NAME_COLUMNS if column in frame.columns), None)
    if id_column is None or name_column is None:
        raise ValueError(
            f"Metadata must contain ID and name fields; found {list(frame.columns)!r}"
        )

    frame = frame.rename(columns={id_column: "id", name_column: "name"})
    frame["id"] = pd.Series(
        pd.to_numeric(frame["id"], errors="raise"), index=frame.index, dtype="int64"
    )
    frame["name"] = frame["name"].astype(str).str.strip()
    frame = frame.drop_duplicates(subset="id", keep="first")

    if ids is not None:
        wanted = [int(pokemon_id) for pokemon_id in ids]
        frame = frame[frame["id"].isin(wanted)]
    return frame.sort_values(by="id").reset_index(drop=True)


def pokemon_ids(ids: Iterable[int] | None = None) -> tuple[int, ...]:
    """Return validated IDs, defaulting to the 151 Gen I Pokédex numbers."""
    values = GEN_I_IDS if ids is None else tuple(int(value) for value in ids)
    if any(value <= 0 for value in values):
        raise ValueError("Pokémon IDs must be positive integers")
    return tuple(dict.fromkeys(values))


def get_pokemon_record(pokemon_id: int, metadata: pd.DataFrame) -> dict[str, object]:
    matches = metadata[metadata["id"] == int(pokemon_id)]
    if matches.empty:
        raise KeyError(f"Pokémon ID {pokemon_id} is not present in metadata")
    return matches.iloc[0].to_dict()


def get_artwork_path(
    pokemon_id: int,
    directory: str | Path | None = None,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve common artwork filenames, falling back to ``<id>.png``."""
    pokemon_id = int(pokemon_id)
    base = project_path(directory) if directory is not None else ARTWORK_DIR.resolve()
    names = (
        f"{pokemon_id}.png",
        f"{pokemon_id:03d}.png",
        f"pokemon_{pokemon_id}.png",
        f"pokemon_{pokemon_id:03d}.png",
    )
    for name in names:
        candidate = base / name
        if candidate.is_file():
            return candidate
    fallback = base / names[0]
    if must_exist:
        raise FileNotFoundError(f"Artwork for Pokémon {pokemon_id} not found under {base}")
    return fallback


def get_silhouette_path(
    pokemon_id: int,
    directory: str | Path | None = None,
) -> Path:
    base = project_path(directory) if directory is not None else SILHOUETTES_DIR.resolve()
    padded = base / f"{int(pokemon_id):03d}.png"
    legacy = base / f"{int(pokemon_id)}.png"
    return padded if padded.is_file() or not legacy.is_file() else legacy


def get_features_path(
    pokemon_id: int,
    directory: str | Path | None = None,
) -> Path:
    base = project_path(directory) if directory is not None else FEATURES_DIR.resolve()
    padded = base / f"{int(pokemon_id):03d}.json"
    legacy = base / f"{int(pokemon_id)}.json"
    return padded if padded.is_file() or not legacy.is_file() else legacy


get_pokemon_image_path = get_artwork_path
artwork_path = get_artwork_path
silhouette_path = get_silhouette_path
features_path = get_features_path

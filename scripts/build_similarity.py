#!/usr/bin/env python3
"""Build normalized silhouettes, features, and all-pairs similarity data.

Run from the project root with:
    python scripts/build_similarity.py

Every unordered pair is scored once with the core module's mirror-aware comparison.
Only the strongest candidates for each target are emitted in both directions, keeping
the packaged similarity index small enough to deploy with the full National Pokédex.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MASK_SIZE = 256
CSV_COLUMNS = (
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
SCORE_COLUMNS = CSV_COLUMNS[4:]


class BuildError(RuntimeError):
    """Raised for invalid inputs or failures in the data-building pipeline."""


@dataclass(order=True, slots=True)
class RankedRow:
    overall_score: float
    neg_similar_id: int
    row: dict[str, Any] = field(compare=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build normalized silhouettes, features, and all-pairs similarity CSV."
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path("data/pokemon/metadata.json"),
        help="download metadata path, relative to project root by default",
    )
    parser.add_argument(
        "--silhouettes-dir",
        type=Path,
        default=Path("data/silhouettes"),
        help="normalized mask output directory",
    )
    parser.add_argument(
        "--features-dir",
        type=Path,
        default=Path("data/features"),
        help="inspectable feature output directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/similarity/similarity.csv"),
        help="similarity CSV path",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenerate existing valid silhouette masks",
    )
    parser.add_argument(
        "--max-neighbors",
        type=int,
        default=40,
        help="closest candidates retained per Pokémon (default: 40; 0 keeps all)",
    )
    args = parser.parse_args()
    if args.max_neighbors < 0:
        parser.error("--max-neighbors cannot be negative")
    return args


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def load_metadata(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise BuildError(
            f"metadata not found at {display_path(path)}; run "
            "`python scripts/download_images.py` first"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read metadata at {display_path(path)}: {exc}") from exc

    # Accept a wrapped form for compatibility, while the downloader writes a plain list.
    if isinstance(value, dict) and isinstance(value.get("pokemon"), list):
        value = value["pokemon"]
    if not isinstance(value, list) or not value:
        raise BuildError(f"{display_path(path)} must contain a non-empty JSON list")

    records: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    problems: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            problems.append(f"record {index} is not an object")
            continue
        pokemon_id = item.get("id")
        name = item.get("name")
        artwork_value = item.get("artwork_path")
        if not isinstance(pokemon_id, int) or pokemon_id < 1:
            problems.append(f"record {index} has invalid id {pokemon_id!r}")
            continue
        if pokemon_id in seen_ids:
            problems.append(f"duplicate Pokémon id {pokemon_id}")
            continue
        seen_ids.add(pokemon_id)
        if not isinstance(name, str) or not name.strip():
            problems.append(f"Pokémon #{pokemon_id} has no valid name")
            continue
        if not isinstance(artwork_value, str) or not artwork_value:
            problems.append(f"Pokémon #{pokemon_id} ({name}) has no artwork_path")
            continue

        artwork_path = project_path(Path(artwork_value))
        if not artwork_path.is_file():
            problems.append(
                f"Pokémon #{pokemon_id} ({name}) artwork is missing: "
                f"{display_path(artwork_path)}"
            )
            continue
        if artwork_path.stat().st_size == 0:
            problems.append(
                f"Pokémon #{pokemon_id} ({name}) artwork is empty: "
                f"{display_path(artwork_path)}"
            )
            continue

        records.append(
            {
                **item,
                "id": pokemon_id,
                "name": name.strip(),
                "_artwork_file": artwork_path,
            }
        )

    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems[:25])
        if len(problems) > 25:
            details += f"\n  - ...and {len(problems) - 25} more"
        raise BuildError(
            "metadata/artwork validation failed; every listed entry must be available:\n"
            + details
        )
    records.sort(key=lambda record: record["id"])
    return records


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(value, file, indent=2, ensure_ascii=False, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_save_mask(mask: Any, path: Path, image_module: Any, numpy_module: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.png")
    try:
        image_module.fromarray(
            (numpy_module.asarray(mask, dtype=numpy_module.uint8) > 0).astype(
                numpy_module.uint8
            )
            * 255,
            mode="L",
        ).save(temporary, format="PNG")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row[column] for column in CSV_COLUMNS})
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_mask(mask: Any, pokemon_id: int, numpy_module: Any) -> Any:
    binary = (numpy_module.asarray(mask) > 0).astype(numpy_module.uint8)
    if binary.shape != (MASK_SIZE, MASK_SIZE):
        raise BuildError(
            f"Pokémon #{pokemon_id} produced mask shape {binary.shape}; "
            f"expected ({MASK_SIZE}, {MASK_SIZE})"
        )
    foreground = int(numpy_module.count_nonzero(binary))
    if foreground == 0 or foreground == binary.size:
        raise BuildError(
            f"Pokémon #{pokemon_id} produced a blank or full-frame silhouette"
        )
    return binary


def load_cached_mask(
    path: Path,
    pokemon_id: int,
    image_module: Any,
    numpy_module: Any,
) -> Any | None:
    if not path.is_file():
        return None
    try:
        with image_module.open(path) as image:
            gray = numpy_module.asarray(image.convert("L"), dtype=numpy_module.uint8)
        return validate_mask(gray, pokemon_id, numpy_module)
    except (OSError, ValueError, BuildError):
        return None


def normalize_scores(result: Any, first_id: int, second_id: int) -> dict[str, float]:
    if not isinstance(result, dict):
        raise BuildError(
            f"comparison for #{first_id}/#{second_id} returned "
            f"{type(result).__name__}, expected a score mapping"
        )
    scores: dict[str, float] = {}
    for column in SCORE_COLUMNS:
        if column not in result:
            raise BuildError(
                f"comparison for #{first_id}/#{second_id} omitted {column!r}"
            )
        try:
            value = float(result[column])
        except (TypeError, ValueError) as exc:
            raise BuildError(
                f"comparison for #{first_id}/#{second_id} returned non-numeric "
                f"{column}={result[column]!r}"
            ) from exc
        if not math.isfinite(value):
            raise BuildError(
                f"comparison for #{first_id}/#{second_id} returned non-finite "
                f"{column}={value}"
            )
        scores[column] = value
    return scores


def main() -> int:
    args = parse_args()
    metadata_path = project_path(args.metadata)
    silhouettes_dir = project_path(args.silhouettes_dir)
    features_dir = project_path(args.features_dir)
    output_path = project_path(args.output)

    try:
        records = load_metadata(metadata_path)
        try:
            import numpy as np
            from PIL import Image

            from src.features import extract_features, serialize_features
            from src.image_processing import process_silhouette
            from src.similarity import compare_silhouettes
        except ImportError as exc:
            missing = exc.name or str(exc)
            raise BuildError(
                f"cannot import project image/similarity modules because {missing!r} is "
                "missing; install the project's dependencies"
            ) from exc

        silhouettes_dir.mkdir(parents=True, exist_ok=True)
        features_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"Loaded {len(records)} Pokémon from {display_path(metadata_path)}", flush=True)
        if len(records) < 31:
            print(
                f"Warning: only {len(records)} Pokémon are available, so each target will "
                f"have {max(0, len(records) - 1)} candidates rather than 30.",
                file=sys.stderr,
            )

        prepared: list[dict[str, Any]] = []
        feature_index: list[dict[str, Any]] = []
        for position, record in enumerate(records, start=1):
            pokemon_id = record["id"]
            name = record["name"]
            artwork_path = record["_artwork_file"]
            mask_path = silhouettes_dir / f"{pokemon_id:03d}.png"
            mask = None
            if not args.force:
                mask = load_cached_mask(mask_path, pokemon_id, Image, np)
            action = "using cached mask" if mask is not None else "building mask"
            print(
                f"[{position:>3}/{len(records)}] #{pokemon_id:>4} {name}: "
                f"{action}; extracting features",
                flush=True,
            )

            if mask is None:
                try:
                    processed = process_silhouette(
                        artwork_path,
                        canvas_size=MASK_SIZE,
                    )
                    mask = validate_mask(processed, pokemon_id, np)
                except (OSError, ValueError) as exc:
                    raise BuildError(
                        f"could not process artwork for #{pokemon_id} ({name}): {exc}"
                    ) from exc
                atomic_save_mask(mask, mask_path, Image, np)

            try:
                features = extract_features(mask)
                flipped_features = extract_features(np.ascontiguousarray(np.fliplr(mask)))
                serialized = serialize_features(features)
            except (KeyError, TypeError, ValueError) as exc:
                raise BuildError(
                    f"could not extract features for #{pokemon_id} ({name}): {exc}"
                ) from exc

            feature_path = features_dir / f"{pokemon_id:03d}.json"
            atomic_write_json(
                feature_path,
                {
                    "id": pokemon_id,
                    "name": name,
                    "artwork_path": display_path(artwork_path),
                    "silhouette_path": display_path(mask_path),
                    "features": serialized,
                },
            )
            feature_index.append(
                {
                    "id": pokemon_id,
                    "name": name,
                    "feature_path": display_path(feature_path),
                }
            )
            prepared.append(
                {
                    "id": pokemon_id,
                    "name": name,
                    "mask": mask,
                    "features": features,
                    "flipped_features": flipped_features,
                }
            )

        atomic_write_json(features_dir / "index.json", feature_index)

        pair_count = len(prepared) * (len(prepared) - 1) // 2
        print(
            f"Calculating {pair_count} unordered pairs with mirror handling...",
            flush=True,
        )
        neighbor_limit = (
            min(args.max_neighbors, max(0, len(prepared) - 1))
            if args.max_neighbors
            else max(0, len(prepared) - 1)
        )
        directed_rows: dict[int, list[RankedRow]] = {
            item["id"]: [] for item in prepared
        }
        completed_pairs = 0
        progress_interval = max(1, min(100, pair_count // 20 if pair_count else 1))
        for first_index, first in enumerate(prepared):
            for second in prepared[first_index + 1 :]:
                try:
                    raw_scores = compare_silhouettes(
                        first["mask"],
                        second["mask"],
                        target_features=first["features"],
                        candidate_features=second["features"],
                        candidate_flipped_features=second["flipped_features"],
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise BuildError(
                        f"could not compare #{first['id']} ({first['name']}) with "
                        f"#{second['id']} ({second['name']}): {exc}"
                    ) from exc
                scores = normalize_scores(raw_scores, first["id"], second["id"])

                first_row = {
                    "target_id": first["id"],
                    "target_name": first["name"],
                    "similar_id": second["id"],
                    "similar_name": second["name"],
                    **scores,
                }
                second_row = {
                    "target_id": second["id"],
                    "target_name": second["name"],
                    "similar_id": first["id"],
                    "similar_name": first["name"],
                    **scores,
                }
                for target_id, row in (
                    (first["id"], first_row),
                    (second["id"], second_row),
                ):
                    # Higher scores are better; for ties, lower Pokédex IDs win.
                    entry = RankedRow(
                        overall_score=float(row["overall_score"]),
                        neg_similar_id=-int(row["similar_id"]),
                        row=row,
                    )
                    heap = directed_rows[target_id]
                    if len(heap) < neighbor_limit:
                        heapq.heappush(heap, entry)
                    elif entry > heap[0]:
                        heapq.heapreplace(heap, entry)
                completed_pairs += 1
                if completed_pairs % progress_interval == 0 or completed_pairs == pair_count:
                    print(
                        f"  pairs: {completed_pairs}/{pair_count} "
                        f"({completed_pairs / pair_count * 100:.1f}%)",
                        flush=True,
                    )

        rows: list[dict[str, Any]] = []
        for item in prepared:
            target_rows = [entry.row for entry in directed_rows[item["id"]]]
            target_rows.sort(
                key=lambda row: (-row["overall_score"], row["similar_id"])
            )
            rows.extend(target_rows)
        atomic_write_csv(output_path, rows)
        print(
            f"Saved {len(rows)} directed rows to {display_path(output_path)} "
            f"({neighbor_limit} candidates per target).", 
            flush=True,
        )
        return 0
    except (BuildError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

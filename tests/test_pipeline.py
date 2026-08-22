from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from scripts import build_similarity
from src.distractors import DIFFICULTY_RANKS, get_distractor_ids
from src.game import end_round, points_for_attempt, record_guess
from src.image_processing import process_silhouette
from src.similarity import compare_silhouettes

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_artwork(kind: str) -> Image.Image:
    image = Image.new("RGBA", (120, 90), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if kind == "asymmetric":
        draw.ellipse((25, 20, 90, 78), fill=(250, 180, 20, 255))
        draw.polygon(((70, 22), (102, 4), (88, 36)), fill=(250, 180, 20, 255))
        draw.rectangle((15, 48, 34, 58), fill=(250, 180, 20, 255))
    elif kind == "rectangle":
        draw.rounded_rectangle((20, 14, 100, 77), radius=8, fill=(20, 160, 220, 255))
    elif kind == "triangle":
        draw.polygon(((60, 5), (108, 80), (12, 80)), fill=(120, 210, 80, 255))
    else:
        raise ValueError(kind)
    return image


class SilhouettePipelineTests(unittest.TestCase):
    def test_retry_scoring_and_timeout_rules(self) -> None:
        state = {
            "completed": False,
            "revealed": False,
            "attempt_count": 0,
            "removed_ids": [],
            "points": 0,
        }
        state = record_guess(state, target_id=25, answer_id=26)
        self.assertFalse(state["completed"])
        self.assertFalse(state["revealed"])
        self.assertEqual(state["removed_ids"], [26])
        self.assertEqual(state["attempt_count"], 1)

        state = record_guess(state, target_id=25, answer_id=27)
        state = record_guess(state, target_id=25, answer_id=25)
        self.assertTrue(state["completed"])
        self.assertTrue(state["revealed"])
        self.assertEqual(state["outcome"], "correct")
        self.assertEqual(state["points"], 1)
        self.assertEqual([points_for_attempt(value) for value in range(1, 5)], [3, 2, 1, 0])

        timed_out = end_round(
            {"completed": False, "revealed": False, "points": 3}, "timed_out"
        )
        self.assertEqual(timed_out["outcome"], "timed_out")
        self.assertTrue(timed_out["revealed"])
        self.assertEqual(timed_out["points"], 0)

    def test_mirroring_and_normalization(self) -> None:
        artwork = make_artwork("asymmetric")
        normal = process_silhouette(artwork)
        mirrored = process_silhouette(artwork.transpose(Image.Transpose.FLIP_LEFT_RIGHT))

        self.assertEqual(normal.shape, (256, 256))
        self.assertEqual(normal.dtype, np.uint8)
        self.assertGreater(int(normal.sum()), 0)
        result = compare_silhouettes(normal, mirrored, return_debug=True)
        self.assertEqual(result["orientation"], "flipped")
        self.assertGreater(float(result["overall_score"]), 0.99)
        self.assertGreater(float(result["iou_score"]), 0.99)
        shift_x, shift_y = result["debug"]["shift"]
        self.assertLessEqual(abs(shift_x), 1)
        self.assertLessEqual(abs(shift_y), 1)

    def test_build_cli_generates_complete_directed_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artwork_dir = root / "artwork"
            artwork_dir.mkdir()
            images = [
                make_artwork("asymmetric"),
                make_artwork("asymmetric").transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                make_artwork("rectangle"),
                make_artwork("triangle"),
            ]
            records = []
            for pokemon_id, image in enumerate(images, start=1):
                artwork_path = artwork_dir / f"{pokemon_id}.png"
                image.save(artwork_path)
                records.append(
                    {
                        "id": pokemon_id,
                        "name": f"fixture-{pokemon_id}",
                        "generation": 1,
                        "types": ["normal"],
                        "artwork_path": str(artwork_path),
                    }
                )
            metadata_path = root / "metadata.json"
            metadata_path.write_text(json.dumps(records), encoding="utf-8")
            masks_dir = root / "masks"
            features_dir = root / "features"
            output_path = root / "similarity.csv"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_similarity.py"),
                    "--metadata",
                    str(metadata_path),
                    "--silhouettes-dir",
                    str(masks_dir),
                    "--features-dir",
                    str(features_dir),
                    "--output",
                    str(output_path),
                    "--jobs",
                    "2",
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((masks_dir / "001.png").is_file())
            self.assertTrue((features_dir / "001.json").is_file())
            self.assertFalse(Path(f"{output_path}.checkpoint.json").exists())

            with output_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 12)
            mirror_row = next(
                row for row in rows if row["target_id"] == "1" and row["similar_id"] == "2"
            )
            self.assertGreater(float(mirror_row["overall_score"]), 0.99)
            self.assertGreater(float(mirror_row["iou_score"]), 0.99)

    def test_similarity_checkpoint_resumes_after_interruption(self) -> None:
        prepared = [
            {
                "id": pokemon_id,
                "name": f"fixture-{pokemon_id}",
                "mask": pokemon_id,
                "features": {},
                "flipped_features": {},
            }
            for pokemon_id in range(1, 6)
        ]
        scoring_config = {"fixture": "deterministic-v1"}

        def score(first: int, second: int, **_: object) -> dict[str, float]:
            value = 1.0 - abs(first - second) / 10.0
            return {column: value for column in build_similarity.SCORE_COLUMNS}

        calls = 0

        def interrupted_score(
            first: int, second: int, **kwargs: object
        ) -> dict[str, float]:
            nonlocal calls
            calls += 1
            if calls == 5:
                raise KeyboardInterrupt
            return score(first, second, **kwargs)

        def empty_heaps() -> dict[int, list[build_similarity.RankedRow]]:
            return {item["id"]: [] for item in prepared}

        def sorted_rows(
            heaps: dict[int, list[build_similarity.RankedRow]],
        ) -> list[tuple[int, int, float]]:
            return sorted(
                (
                    target_id,
                    int(entry.row["similar_id"]),
                    float(entry.row["overall_score"]),
                )
                for target_id, heap in heaps.items()
                for entry in heap
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint_path = root / "resume.checkpoint.json"
            interrupted_heaps = empty_heaps()
            with self.assertRaises(KeyboardInterrupt):
                build_similarity.calculate_similarities(
                    prepared,
                    interrupted_heaps,
                    0,
                    2,
                    checkpoint_path=checkpoint_path,
                    checkpoint_interval=2,
                    max_neighbors=2,
                    scoring_config=scoring_config,
                    jobs=1,
                    scorer=interrupted_score,
                )
            self.assertTrue(checkpoint_path.is_file())

            completed_pairs, resumed_heaps = build_similarity.load_checkpoint(
                checkpoint_path,
                prepared,
                2,
                2,
                scoring_config,
            )
            self.assertEqual(completed_pairs, 4)
            self.assertEqual(
                json.loads(checkpoint_path.read_text(encoding="utf-8"))["next_pair"],
                [1, 2],
            )
            completed_pairs = build_similarity.calculate_similarities(
                prepared,
                resumed_heaps,
                completed_pairs,
                2,
                checkpoint_path=checkpoint_path,
                checkpoint_interval=2,
                max_neighbors=2,
                scoring_config=scoring_config,
                jobs=1,
                scorer=score,
            )
            self.assertEqual(completed_pairs, 10)

            baseline_heaps = empty_heaps()
            build_similarity.calculate_similarities(
                prepared,
                baseline_heaps,
                0,
                2,
                checkpoint_path=root / "baseline.checkpoint.json",
                checkpoint_interval=20,
                max_neighbors=2,
                scoring_config=scoring_config,
                jobs=1,
                scorer=score,
            )
            self.assertEqual(sorted_rows(resumed_heaps), sorted_rows(baseline_heaps))
            with self.assertRaisesRegex(build_similarity.BuildError, "max_neighbors"):
                build_similarity.load_checkpoint(
                    checkpoint_path,
                    prepared,
                    2,
                    3,
                    scoring_config,
                )
            with self.assertRaisesRegex(build_similarity.BuildError, "scoring_config"):
                build_similarity.load_checkpoint(
                    checkpoint_path,
                    prepared,
                    2,
                    2,
                    {"fixture": "changed"},
                )
            with self.assertRaisesRegex(build_similarity.BuildError, "pokemon"):
                build_similarity.load_checkpoint(
                    checkpoint_path,
                    list(reversed(prepared)),
                    2,
                    2,
                    scoring_config,
                )

    def test_distractor_rank_bands_are_unique(self) -> None:
        rows = [
            {
                "target_id": 1,
                "target_name": "target",
                "similar_id": pokemon_id,
                "similar_name": f"candidate-{pokemon_id}",
                "overall_score": 1.0 - rank / 100,
                "contour_score": 1.0 - rank / 100,
                "iou_score": 1.0 - rank / 100,
                "radial_score": 1.0 - rank / 100,
                "geometric_score": 1.0 - rank / 100,
            }
            for rank, pokemon_id in enumerate(range(2, 52), start=1)
        ]
        frame = pd.DataFrame(rows)
        for difficulty, (start, configured_end) in DIFFICULTY_RANKS.items():
            selected = get_distractor_ids(
                1,
                difficulty=difficulty,
                count=3,
                similarity_data=frame,
                random_state=7,
            )
            self.assertEqual(len(selected), 3)
            self.assertEqual(len(set(selected)), 3)
            ranks = [pokemon_id - 1 for pokemon_id in selected]
            end = configured_end or 3
            self.assertTrue(all(start <= rank <= end for rank in ranks))


if __name__ == "__main__":
    unittest.main()

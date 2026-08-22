from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GameAppTests(unittest.TestCase):
    def test_expert_retry_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            pokemon_dir = data_dir / "pokemon"
            artwork_dir = pokemon_dir / "artwork"
            masks_dir = data_dir / "silhouettes"
            similarity_dir = data_dir / "similarity"
            for directory in (artwork_dir, masks_dir, similarity_dir):
                directory.mkdir(parents=True)

            metadata = []
            for pokemon_id in range(1, 5):
                artwork_path = artwork_dir / f"{pokemon_id}.png"
                Image.new("RGBA", (64, 64), (pokemon_id * 40, 100, 140, 255)).save(
                    artwork_path
                )
                mask = np.zeros((256, 256), dtype=np.uint8)
                mask[48:208, 58 + pokemon_id : 198 + pokemon_id] = 255
                Image.fromarray(mask).save(masks_dir / f"{pokemon_id:03d}.png")
                metadata.append(
                    {
                        "id": pokemon_id,
                        "name": f"fixture-{pokemon_id}",
                        "generation": 1,
                        "types": ["normal"],
                        "artwork_path": str(artwork_path),
                    }
                )
            (pokemon_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

            columns = [
                "target_id",
                "target_name",
                "similar_id",
                "similar_name",
                "overall_score",
                "contour_score",
                "iou_score",
                "radial_score",
                "geometric_score",
            ]
            with (similarity_dir / "similarity.csv").open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=columns)
                writer.writeheader()
                for target_id in range(1, 5):
                    for similar_id in range(1, 5):
                        if target_id == similar_id:
                            continue
                        score = 1.0 - abs(target_id - similar_id) / 10
                        writer.writerow(
                            {
                                "target_id": target_id,
                                "target_name": f"fixture-{target_id}",
                                "similar_id": similar_id,
                                "similar_name": f"fixture-{similar_id}",
                                "overall_score": score,
                                "contour_score": score,
                                "iou_score": score,
                                "radial_score": score,
                                "geometric_score": score,
                            }
                        )

            previous_data_dir = os.environ.get("POKEGAME_DATA_DIR")
            os.environ["POKEGAME_DATA_DIR"] = str(data_dir)
            try:
                app = AppTest.from_file(PROJECT_ROOT / "app.py").run(timeout=15)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.selectbox), 0)
                initial_round = app.session_state["home_round"]
                initial_target_id = int(initial_round["question"]["target_id"])
                correct_label = f"Fixture {initial_target_id}"
                initial_deadline = float(initial_round["deadline"])

                answer_buttons = [
                    button
                    for button in app.button
                    if button.label.startswith("Fixture ")
                ]
                self.assertEqual(len(answer_buttons), 4)
                wrong_button = next(
                    button for button in answer_buttons if button.label != correct_label
                )
                wrong_label = wrong_button.label
                wrong_button.click().run(timeout=15)

                remaining_labels = [button.label for button in app.button]
                self.assertNotIn(wrong_label, remaining_labels)
                self.assertIn(correct_label, remaining_labels)
                self.assertTrue(any("has been removed" in item.value for item in app.warning))

                next(button for button in app.button if button.label == correct_label).click().run(
                    timeout=15
                )
                self.assertTrue(any("+2 points" in item.value for item in app.success))
                advanced_round = app.session_state["home_round"]
                self.assertEqual(float(advanced_round["deadline"]), initial_deadline)
                self.assertNotEqual(
                    int(advanced_round["question"]["target_id"]), initial_target_id
                )
                metric_values = {metric.label: metric.value for metric in app.metric}
                self.assertEqual(metric_values["Score"], "2")
                self.assertEqual(metric_values["Pokémon caught"], "1")
                next_answer_buttons = [
                    button for button in app.button if button.label.startswith("Fixture ")
                ]
                self.assertEqual(len(next_answer_buttons), 4)

                advanced_round["deadline"] = 0.0
                app.session_state["home_round"] = advanced_round
                next_answer_buttons[0].click().run(timeout=15)
                self.assertTrue(any("Time's up" in item.value for item in app.error))
                self.assertTrue(any(button.label == "Play again" for button in app.button))
                self.assertFalse(
                    any(button.label.startswith("Fixture ") for button in app.button)
                )
            finally:
                if previous_data_dir is None:
                    os.environ.pop("POKEGAME_DATA_DIR", None)
                else:
                    os.environ["POKEGAME_DATA_DIR"] = previous_data_dir


if __name__ == "__main__":
    unittest.main()

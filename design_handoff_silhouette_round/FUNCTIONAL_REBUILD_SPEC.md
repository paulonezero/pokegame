# Pokémon Silhouette Game — Functional Rebuild Specification

This document is a functional handoff for rebuilding the application in a new implementation session. It describes the product goals, game behavior, data pipeline, state transitions, runtime contracts, tests, and deployment requirements.

It intentionally does **not** prescribe visual design, page structure, spacing, typography, responsive breakpoints, component placement, or other presentation decisions. The next implementation should design the player experience independently rather than copying the presentation code in `app.py`.

## 1. Product goal

Build a fast, replayable game in which a player identifies as many Generation I Pokémon silhouettes as possible during one continuous 30-second round.

The product should:

- Use all 151 Generation I Pokémon.
- Show one silhouette and four possible Pokémon names per new question.
- Choose plausible wrong answers based on silhouette similarity rather than arbitrary random names.
- Let the player retry after an incorrect answer.
- Award fewer points after each incorrect attempt.
- Immediately continue to another Pokémon after a correct answer.
- Preserve one shared timer across the entire round.
- Reveal and summarize the result when time expires.
- Work entirely from packaged local data during normal gameplay.
- Require no account, API key, database, external service, or network request at runtime.

## 2. Presentation is deliberately unspecified

The rebuild must preserve the functionality in this specification, but it does not need to preserve the current Streamlit presentation layer.

The following are open design decisions for the next session:

- Application framework and frontend architecture.
- How a player starts a round.
- Navigation and screen transitions.
- Information hierarchy and component arrangement.
- Visual identity, animation, feedback treatment, and interaction details.
- Responsive behavior and accessibility implementation.

The UX session should treat the functional modules and packaged data as backend/domain assets. It may replace `app.py` completely.

## 3. Current technology and prerequisites

The existing implementation uses:

- Python 3.12+
- Streamlit 1.x
- NumPy
- pandas
- Pillow
- OpenCV (`opencv-python-headless`)

Dependencies are declared in `requirements.txt`.

A rebuild may keep Streamlit or introduce a different frontend, but the existing Python data and game modules can be reused independently of most presentation decisions. If the framework changes, preserve the behavioral contracts and replace the Streamlit-specific cache/runtime wrappers where necessary.

## 4. Repository responsibilities

```text
app.py                         Current presentation and round orchestration
src/config.py                  Paths and similarity defaults
src/pokemon_data.py            General metadata/path helpers
src/image_processing.py        Artwork-to-silhouette processing
src/features.py                Silhouette feature extraction
src/similarity.py              Pairwise silhouette scoring
src/distractors.py             Difficulty-based wrong-answer selection
src/game.py                    Pure guess/scoring state transitions
src/ui_data.py                 Runtime data validation and image loading
scripts/download_images.py     PokéAPI metadata/artwork acquisition
scripts/build_similarity.py    Silhouette, feature, and similarity generation
data/pokemon/metadata.json     Packaged Pokémon metadata
data/pokemon/artwork/          Packaged official artwork
data/silhouettes/              Packaged normalized masks
data/similarity/similarity.csv Packaged pairwise similarity index
tests/test_pipeline.py         Domain and data-pipeline tests
tests/test_app.py              Current end-to-end Streamlit behavior test
```

For a presentation rebuild, prefer keeping reusable domain/data code under `src/` separate from the new view layer.

## 5. Runtime data contract

Normal gameplay must use committed local assets. It should not call PokéAPI or recompute pairwise similarity when the application starts.

The packaged Generation I dataset contains:

- 151 Pokémon metadata records.
- 151 transparent official-artwork PNGs.
- 151 normalized silhouette PNGs.
- 22,650 directed similarity rows: 151 × 150 candidates.

### 5.1 Metadata

Default path:

```text
data/pokemon/metadata.json
```

The downloader writes a JSON list. Each record has at least:

```json
{
  "id": 25,
  "name": "pikachu",
  "generation": 1,
  "types": ["electric"],
  "artwork_path": "data/pokemon/artwork/25.png"
}
```

Runtime loading should:

1. Validate that metadata exists and is readable JSON.
2. Normalize IDs to integers.
3. Normalize names for player-facing use.
4. Resolve artwork paths locally.
5. Ignore duplicate or unusable records.
6. Select records explicitly marked as generation 1; if generation is absent, IDs 1–151 are the fallback definition.
7. Require at least four usable records before a question can be built.

`POKEGAME_DATA_DIR` may override the default `data/` root for tests or alternate packaged datasets.

### 5.2 Silhouettes

Default path pattern:

```text
data/silhouettes/{id:03d}.png
```

Examples:

```text
data/silhouettes/001.png
data/silhouettes/025.png
data/silhouettes/151.png
```

Each mask is:

- A 256×256 grayscale PNG.
- Binary foreground/background data.
- Non-empty and not full-frame.
- Aspect-preserving.
- Centered on a square canvas.
- Built from the transparent artwork's alpha channel.

A legacy unpadded filename such as `25.png` may be accepted as a fallback.

### 5.3 Similarity index

Preferred path:

```text
data/similarity/similarity.csv
```

Required columns:

```text
target_id
target_name
similar_id
similar_name
overall_score
contour_score
iou_score
radial_score
geometric_score
```

Requirements:

- IDs are integers.
- Score columns are numeric values in the range 0–1.
- A target cannot be its own candidate.
- Candidate IDs must be unique for a target after loading/deduplication.
- Runtime records should be filtered to Pokémon available in metadata.
- Every playable target needs at least three usable similarity rows.

## 6. One-time data acquisition

This stage is only needed when regenerating the packaged dataset.

Run from the project root:

```bash
python scripts/download_images.py
```

### 6.1 Downloader behavior

For Pokémon IDs 1–151 by default:

1. Request Pokémon data from `https://pokeapi.co/api/v2/pokemon/{id}`.
2. Follow the species URL to determine generation.
3. Read the canonical name and ordered types.
4. Download transparent official artwork from the PokéAPI sprite repository.
5. Fall back to the official-artwork URL in the Pokémon response when needed.
6. Verify that artwork bytes are PNG data.
7. Save artwork as `data/pokemon/artwork/{id}.png`.
8. Save/update `data/pokemon/metadata.json` after every completed item so interrupted runs are resumable.
9. Use atomic file replacement to avoid leaving partial data.
10. Retry transient network failures with bounded exponential backoff.

Useful options:

```bash
python scripts/download_images.py --start-id 1 --end-id 151
python scripts/download_images.py --force
python scripts/download_images.py --timeout 20 --retries 3 --retry-delay 1
```

This stage has network, availability, and third-party asset implications. Runtime gameplay does not.

## 7. Silhouette generation

Implemented in `src/image_processing.py` and orchestrated by `scripts/build_similarity.py`.

For each transparent artwork image:

1. Read the alpha channel.
2. Convert alpha values above the configured threshold to a binary foreground mask.
3. Reject images without transparency or without foreground pixels.
4. Crop the mask to its foreground bounding box.
5. Resize it proportionally into a 256×256 canvas.
6. Leave 16 pixels of nominal padding around the available drawing area.
7. Use nearest-neighbor interpolation so the mask stays binary.
8. Center the resized foreground.
9. Save as an 8-bit grayscale PNG with values 0 and 255.

Defaults from `src/config.py`:

```python
DEFAULT_CANVAS_SIZE = 256
DEFAULT_PADDING = 16
DEFAULT_ALPHA_THRESHOLD = 0
```

The processing must preserve aspect ratio and must not infer the silhouette from RGB color; the alpha channel is authoritative.

## 8. Feature extraction

Implemented in `src/features.py`.

For every normalized silhouette, extract:

- Largest external contour.
- Robust interior center using a Euclidean distance transform.
- A normalized 360-sample radial contour profile.
- Normalized bounding-box width.
- Normalized bounding-box height.
- Aspect ratio.
- Foreground area ratio.
- Normalized contour perimeter.
- Compactness.
- Convex-hull fill ratio.

The builder writes inspectable JSON feature files to:

```text
data/features/{id:03d}.json
```

It also writes:

```text
data/features/index.json
```

Feature files are useful for inspection and rebuilding. Normal gameplay currently relies on the precomputed CSV and does not need to load all feature JSON files.

## 9. Pairwise similarity generation

Run:

```bash
python scripts/build_similarity.py
```

The builder must:

1. Validate every metadata record and artwork file before processing.
2. Reuse valid cached silhouette masks unless `--force` is supplied.
3. Build masks and feature JSON files.
4. Compare each unordered pair exactly once.
5. Evaluate the candidate in both normal and horizontally mirrored orientations.
6. Apply all score components to each orientation coherently.
7. Keep the orientation with the higher weighted total; ties prefer the original orientation.
8. Emit the resulting scores in both target/candidate directions.
9. Sort each target's candidates by descending overall score, then candidate ID.
10. Write the complete similarity CSV atomically.

For 151 Pokémon:

- Unordered comparisons: 151 × 150 ÷ 2 = 11,325.
- Directed output rows: 22,650.
- Candidates per target: 150.

### 9.1 Similarity components

The score combines:

1. **Contour similarity** — OpenCV `matchShapes()` converted to a 0–1 similarity.
2. **Shifted intersection-over-union** — best mask IoU after translations of up to ±5 pixels on each axis.
3. **Radial similarity** — cosine similarity between 360-sample normalized radial profiles.
4. **Geometric similarity** — mean relative similarity across width, height, aspect ratio, area, perimeter, compactness, and convex-hull fill.

Default weights from `src/config.py`:

```python
DEFAULT_WEIGHTS = {
    "contour": 0.40,
    "iou": 0.30,
    "radial": 0.20,
    "geometric": 0.10,
}
```

Weights must be finite and non-negative, and are normalized to sum to one. An all-zero set falls back to the defaults.

The data format retains each component so overall scores can be reweighted without recomputing image features.

## 10. Distractor selection

Implemented in `src/distractors.py`.

Candidates are ranked by descending `overall_score` for the target. Select unique wrong answers randomly from a difficulty-specific rank band:

| Difficulty | Candidate ranks |
|---|---:|
| Easy | 15–40 |
| Normal | 5–20 |
| Hard | 2–10 |
| Expert | 1–5 |

The current game always uses:

```python
DIFFICULTY = "expert"
```

Each question needs exactly three distractors. For expert mode, sample three unique candidates from the five most similar silhouettes.

Selection requirements:

- Never include the target itself.
- Never return duplicate IDs.
- Raise an actionable error if fewer than three candidates are available.
- Support a deterministic random seed so a question's options remain stable across reruns/re-renders.

## 11. Question generation

A new question contains:

```python
{
    "target_id": int,
    "question_number": int,
    "answers": list[int],
    "removed_ids": list[int],
    "attempt_count": int,
    "selected_id": int | None,
    "last_wrong_id": int | None,
    "completed": bool,
    "revealed": bool,
    "outcome": str | None,
    "points": int,
    "error": str | None,
}
```

Build it as follows:

1. Select one target from the round's remaining shuffled targets.
2. Select three expert distractors from the similarity index.
3. Add the target ID to the three distractor IDs.
4. Shuffle the four answers deterministically.
5. Initialize attempts, removed answers, result fields, and points.

### 11.1 Deterministic question seed

The current implementation derives a seed from:

```text
{target_id}:expert:{round_number}:{question_number}:pokegame-v3
```

It computes SHA-256, takes the first eight digest bytes, and interprets them as a big-endian integer.

Use the same seed for:

- Sampling distractors.
- Shuffling the four answer IDs.

This matters in rerun-based frameworks such as Streamlit: the answer set and order must not change just because the view rerendered.

A rebuild may use an equivalent persisted-question approach instead of this exact seed algorithm, provided the stability guarantee remains true.

## 12. Guess and scoring rules

Pure game transitions are implemented in `src/game.py`.

### 12.1 Incorrect guess

When the chosen answer is not the target:

1. Increment `attempt_count`.
2. Store the selected answer ID.
3. Add it to `removed_ids`.
4. Store it as `last_wrong_id`.
5. Keep the question active.
6. Award zero points for that attempt.
7. Do not reveal the target.
8. Do not reset or extend the round deadline.

A removed answer cannot be submitted again.

### 12.2 Correct guess

When the chosen answer is the target:

1. Increment `attempt_count`.
2. Complete and reveal the question.
3. Set outcome to `correct`.
4. Clear the last wrong-answer marker.
5. Award points according to the attempt number.
6. Add those points to the round score.
7. Increment the round's identified-Pokémon count.
8. Advance immediately to another target.
9. Preserve the existing round deadline.

### 12.3 Points

| Correct on attempt | Points |
|---:|---:|
| 1 | 3 |
| 2 | 2 |
| 3 | 1 |
| 4 or later | 0 |

With four initial choices and incorrect choices removed, the fourth remaining answer will be the target and is worth zero points.

## 13. Round behavior

A round lasts 30 seconds total.

Suggested round-state contract:

```python
{
    "signature": str,
    "round_number": int,
    "deadline": float,
    "completed": bool,
    "score": int,
    "correct_count": int,
    "question_number": int,
    "remaining_targets": list[int],
    "last_result": dict | None,
    "question": dict,
}
```

### 13.1 Starting a round

When play begins:

1. Increment a session round number.
2. Set an absolute deadline to current time + 30 seconds.
3. Reset score and correct count to zero.
4. Shuffle the available Pokémon IDs using a non-deterministic random source.
5. Pop one target and create question 1.

Whether play starts automatically or after an explicit player action is a UX decision. The deadline must not begin before the player can reasonably interact with the round.

### 13.2 Target sequence

- Use each available target at most once before refilling the pool.
- When the pool is exhausted, reshuffle all targets.
- Avoid immediately repeating the current target when refilling.

### 13.3 Timer

Use an absolute deadline, not a decrement-only counter. This prevents rerenders, browser delays, or processing time from extending the round.

Requirements:

- Display or otherwise expose the remaining time to the player.
- Refresh the remaining-time state approximately once per second.
- Clamp displayed remaining time to zero.
- Before accepting any guess, compare the current time with the deadline.
- If the deadline has passed, complete the round instead of processing the guess.
- Correct answers never reset the deadline.

### 13.4 Completing a round

When time reaches zero:

1. Mark the round completed.
2. Stop accepting answer submissions.
3. Reveal the final target's identity.
4. Make its full artwork available for the result state.
5. Report total score.
6. Report number of Pokémon identified.
7. Update the best score for the current browser/application session.
8. Allow another fresh round to be started.

The domain helper also supports a `gave_up` outcome, but the current product does not expose a give-up action. Adding one is optional product scope, not a rebuild requirement.

## 14. Required player-facing capabilities

The new presentation must provide access to the following functionality, without prescribing how it is arranged or styled:

During an active round:

- Current silhouette.
- Four candidate names for a fresh question.
- Fewer candidates after incorrect choices are removed.
- Remaining round time.
- Current score.
- Count of correctly identified Pokémon.
- Current question number.
- Current attempt number.
- Points available for the next correct guess.
- Clear indication that an answer was incorrect and removed.
- Clear indication that an answer was correct and how many points were awarded.

After expiration:

- Time-expired state.
- Final Pokémon identity and artwork.
- Final score.
- Total correctly identified Pokémon.
- Best score for the session.
- Ability to start another round.

For unavailable or malformed data:

- An actionable setup error rather than an unhandled exception.

Exactly how these capabilities are communicated is part of the new UX design.

## 15. Runtime loading and failure handling

At application startup:

1. Load and normalize metadata.
2. Filter to Generation I.
3. Require at least four records.
4. Locate and validate a similarity CSV.
5. Filter similarity rows to available metadata IDs.
6. Build an ID-to-record lookup.
7. Initialize or restore valid session state.

Handle these failures explicitly:

- Metadata file missing.
- Invalid JSON metadata.
- No usable metadata records.
- Fewer than four usable Pokémon.
- Similarity CSV missing or malformed.
- Required similarity columns missing.
- Target with fewer than three valid distractors.
- Missing silhouette mask.
- Empty silhouette mask.
- Missing final artwork.
- Corrupt session/question state.

Missing final artwork should not prevent the score summary from being shown. Missing gameplay silhouettes or insufficient distractors should prevent an invalid question from being played.

## 16. Caching and performance

Normal gameplay should avoid repeated disk and CPU work.

Cache or memoize as appropriate:

- Parsed metadata.
- Parsed similarity CSV.
- Decoded artwork images.
- Loaded silhouette masks or rendered silhouette images.

Do not cache mutable round state globally across players. Player state belongs to the current session.

Do not perform OpenCV pair comparisons during gameplay. Distractor selection should be a lookup and sample operation over the precomputed CSV.

## 17. Testing requirements

Run the current test suite with:

```bash
python -m unittest discover -s tests -v
```

### 17.1 Domain tests

Preserve tests for:

- First-, second-, third-, and fourth-attempt scores: `[3, 2, 1, 0]`.
- Incorrect answers being removed exactly once.
- Correct answers completing a question.
- Timeout outcomes awarding zero points.
- Silhouette output being a non-empty 256×256 `uint8` mask.
- Mirrored artwork matching its original with a flipped orientation and near-perfect score.
- Similarity builder producing complete directed pair output.
- Difficulty rank bands returning unique candidates from the correct ranges.

### 17.2 Round-flow integration tests

Preserve an end-to-end test that verifies:

1. A new question has four answer choices.
2. Choosing a wrong answer removes it.
3. The correct answer remains available.
4. A correct second guess awards two points.
5. A correct answer advances to a different target.
6. The absolute deadline is unchanged after advancement.
7. Score and identified count update.
8. A new question again has four choices.
9. A guess after expiration completes the round rather than being scored.
10. No answer actions remain active after expiration.
11. Another round can be started.

`tests/test_app.py` currently implements this with Streamlit's `AppTest`. If the framework changes, replace it with an equivalent integration test rather than preserving Streamlit-specific assertions.

### 17.3 Data-builder test

Use a small temporary fixture set to verify that:

- Metadata and artwork are accepted.
- Masks and feature files are generated.
- Four items produce 12 directed rows.
- A mirrored pair receives near-perfect overall and IoU scores.

## 18. Recommended implementation order for the rebuild

1. **Choose the new presentation architecture.** Decide whether to retain Streamlit or add a separate frontend, without modifying domain behavior yet.
2. **Keep or port the data contracts.** Confirm metadata, mask, and similarity loaders work in the chosen runtime.
3. **Keep the pure game rules.** Reuse or port `points_for_attempt()` and `record_guess()` with unit tests first.
4. **Implement round state.** Use an absolute deadline, target pool, score, count, and persisted current question.
5. **Implement stable question creation.** Select a target, choose three expert distractors, and persist/shuffle four answers.
6. **Implement answer commands.** Reject expired guesses, remove wrong answers, score correct answers, and advance.
7. **Implement expiration.** Stop interactions, reveal the final target, summarize results, and update session best.
8. **Connect packaged images.** Serve/load silhouettes during play and artwork after expiration.
9. **Add setup validation.** Fail clearly when packaged data is incomplete.
10. **Build the new UX around the required capabilities in section 14.** Do not copy the current page composition by default.
11. **Add framework-level integration tests.** Cover retry, advancement, deadline preservation, timeout, and replay.
12. **Test on target devices and deployment.** Functional correctness and accessibility should be verified in the actual runtime environment.

## 19. Local development

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the current implementation:

```bash
streamlit run app.py
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

If the presentation framework changes, replace the run command and document it while retaining a straightforward one-command local workflow.

## 20. Deployment requirements

The current app is suitable for Streamlit Community Cloud because it is a persistent Python Streamlit application.

For any replacement deployment:

- Package all runtime metadata, artwork, silhouettes, and similarity rows.
- Ensure local asset paths resolve independently of the process working directory.
- Do not require data generation during deployment startup.
- Do not require secrets.
- Preserve per-session game state isolation.
- Use a platform capable of running the selected backend/runtime; a static host alone cannot run a Python server application.

## 21. Legal and data-source note

Metadata and artwork originate from PokéAPI and its sprite repository. Pokémon names, characters, and artwork are trademarks or copyrighted works of Nintendo, Creatures Inc., and GAME FREAK.

The project is an unofficial fan prototype and is not affiliated with or endorsed by those rights holders. Asset-usage requirements should be reviewed before public or commercial deployment.

## 22. Functional definition of done

The rebuild is functionally complete when:

- A player can complete repeated 30-second rounds.
- Every new question has one correct Gen I target and three expert similarity-based distractors.
- Incorrect options are removed without resetting the timer.
- Correct answers score 3/2/1/0 by attempt and immediately advance.
- Targets do not repeat until the pool is exhausted.
- The timer cannot be extended by rerenders or answer processing.
- Expiration prevents late guesses and produces a complete result summary.
- Best score persists for the session.
- Gameplay performs no external network calls and no pairwise image analysis.
- Missing data produces actionable errors.
- Domain, pipeline, and full round-flow tests pass.
- The new presentation was designed independently from the existing `app.py` layout.

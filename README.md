# Pokémon Silhouette Game

A player-facing Streamlit game using all 151 Generation I Pokémon. Players identify as many silhouettes as possible during one continuous 30-second round.

## Game rules

- Pokémon are selected randomly without repetition.
- Every question has the correct name and three Expert-level silhouette matches.
- An incorrect answer disappears and the player can guess again.
- A correct answer immediately advances to the next random Pokémon without resetting the timer.
- Correct on guess 1: **3 points**
- Correct on guess 2: **2 points**
- Correct on guess 3: **1 point**
- Correct on guess 4: **0 points**
- The round ends when the shared 30-second timer expires.
- The final score, number identified, and best score for the browser session are shown.

The interface is a single player page. It uses compact spacing, a 2×2 touch-friendly answer grid, and responsive image sizing to fit iPad portrait and landscape viewports without normal gameplay scrolling.

## Included Pokémon pool

The packaged deployment data contains:

- **151 Pokémon** — Pokédex IDs 1–151
- **151 official-artwork images**
- **151 normalized silhouettes**
- **22,650 directed similarity rows** — 150 candidates for every target

Runtime data is committed under `data/`, so a deployment does not call PokéAPI or rebuild similarities when it starts.

## Run locally

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Community Cloud

Streamlit Community Cloud is the recommended host because this is a persistent Python Streamlit application. Netlify is primarily for static/front-end applications and cannot run this app directly without a separately hosted Python backend.

1. Push the project, including the runtime `data/` files, to a GitHub repository.
2. Visit [share.streamlit.io](https://share.streamlit.io/).
3. Select **Create app** and connect the GitHub repository.
4. Choose the branch containing this project.
5. Set the main file path to `app.py`.
6. Deploy. No secrets or external services are required.

Deployment settings are included in:

- `.streamlit/config.toml`
- `.python-version`
- `requirements.txt`

The app uses approximately 24 MB of packaged runtime artwork, silhouettes, metadata, and similarity data. The full local `data/` directory is approximately 32 MB because it also contains ignored build-only feature caches.

## Regenerate the data

The runtime bundle is already included. To refresh it from PokéAPI:

```bash
python scripts/download_images.py
python scripts/build_similarity.py
```

The downloader is resumable and supports partial ID ranges and `--force`. The builder processes transparent artwork, creates centered 256×256 masks, extracts features, compares all 11,325 unordered Gen I pairs, and writes both directed rows for each pair.

## Similarity model

Default weights are defined in `src/config.py`:

```python
DEFAULT_WEIGHTS = {
    "contour": 0.40,
    "iou": 0.30,
    "radial": 0.20,
    "geometric": 0.10,
}
```

The score combines:

1. OpenCV contour matching with `cv2.matchShapes()`.
2. Best pixel-mask IoU over translations of ±5 pixels.
3. A normalized 360-sample radial silhouette profile.
4. Width, height, aspect ratio, area, perimeter, compactness, and convex-hull fill.

Normal and horizontally mirrored candidate orientations are evaluated, and the better complete orientation is retained.

## Project layout

```text
app.py                         Player game
.streamlit/config.toml         Streamlit deployment/theme settings
src/ui_data.py                 Runtime metadata and image loading
src/game.py                    Guess and point rules
src/distractors.py             Expert distractor selection
src/image_processing.py        Silhouette generation
src/features.py                Feature extraction
src/similarity.py              Pair scoring
scripts/download_images.py     PokéAPI data setup
scripts/build_similarity.py    Similarity precomputation
data/pokemon/                  Metadata and artwork
data/silhouettes/              Runtime masks
data/similarity/               Runtime similarity index
tests/                         Pipeline and player-flow tests
```

## Validation

Run:

```bash
python -m unittest discover -s tests -v
```

The tests cover silhouette normalization, mirror matching, full build output, distractor rank bands, retry scoring, automatic random advancement, preservation of the shared deadline, and round expiration.

## Data source and notice

Metadata and artwork are sourced from [PokéAPI](https://pokeapi.co/) and its sprite repository. Pokémon names, characters, and artwork are trademarks or copyrighted works of Nintendo, Creatures Inc., and GAME FREAK. This project is an unofficial fan prototype and is not affiliated with or endorsed by those rights holders. Review the applicable asset-usage requirements before a public or commercial launch.

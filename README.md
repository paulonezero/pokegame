# Poké-Guesser

A 30-second Pokémon silhouette game covering every packaged National Pokédex species. The application uses a FastAPI backend for authoritative round state and a React frontend for the responsive player experience. Players choose a password-free browser identity and compete on a persistent global top-10 leaderboard where every qualifying round is listed.

Gameplay is fully local at runtime: metadata, silhouettes, artwork, and the precomputed similarity index are packaged under `data/`. No PokéAPI requests or pairwise image comparisons occur while playing.

Rounds with at least three correctly named Pokémon and no wrong selections receive a 2× score bonus at time-up. The doubled total is the score submitted to the global leaderboard.

Every three correct answers since the last wrong selection also award +5 points. This counter resets after each award or wrong answer, so the bonus can be earned repeatedly within one round and is included before any flawless-round multiplier.

Equal leaderboard scores are ordered by more Pokémon named, fewer wrong selections, higher best streak, and finally the earlier submission time. These tie-break details are shown beneath each player name.

## Requirements

- Python 3.12+
- Node.js 20+

## Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm --prefix frontend install
```

## Develop

Run both FastAPI and Vite with one command:

```bash
python scripts/dev.py
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` requests to FastAPI at `http://127.0.0.1:8000`.

## Production-style local run

Build the frontend, then let FastAPI serve it and the API from one process:

```bash
npm --prefix frontend run build
uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Deploy to Railway

The repository includes `Dockerfile` and `railway.json`, so Railway builds the React frontend and serves it from the same FastAPI service.

1. Push the repository, including the packaged `data/` assets, to GitHub.
2. In Railway, choose **New Project → Deploy from GitHub repo** and select this repository.
3. Leave the service root directory blank (the repository root).
4. Railway will detect `railway.json` and build with `Dockerfile`.
5. In **Settings → Networking**, choose **Generate Domain**.
6. Attach a Railway volume to the service with the mount path `/data`. The app automatically stores its SQLite leaderboard at `/data/leaderboard.sqlite3` using Railway's `RAILWAY_VOLUME_MOUNT_PATH` variable.
7. Keep the service at one replica because browser sessions are stored in process memory and Railway volumes do not support replicas.

No manually configured application variables are required. Railway supplies `PORT` and the volume mount path, and the container starts one Uvicorn worker automatically. Enable volume backups in Railway if leaderboard recovery is important.

To test the same container locally:

```bash
docker build -t pokegame .
docker run --rm -p 8000:8000 pokegame
```

Then open `http://127.0.0.1:8000`.

Session-best scores still reset when the process restarts, while global leaderboard scores survive on the SQLite volume. To scale beyond one replica, move sessions and leaderboard storage to shared services such as Redis and Postgres first.

For local development, leaderboard data is written to the ignored `.runtime/leaderboard.sqlite3` file. Set `POKEGAME_LEADERBOARD_DB_PATH` to use a different SQLite path.

## Build the Pokémon data

Runtime data is generated ahead of deployment so gameplay never depends on PokéAPI. To add every currently available National Pokédex species after the original 151:

```bash
python scripts/download_images.py --start-id 152 --end-id 1025
python scripts/build_similarity.py
```

The downloader saves each completed Pokémon and can safely be rerun after an interruption. The similarity build keeps the closest 40 candidates per Pokémon, covering every supported difficulty band without packaging the full all-pairs matrix. PokéAPI form IDs (regional forms, Mega Evolutions, and similar variants) are intentionally not included in the National Pokédex range.

The similarity calculation uses all available CPU cores by default; pass `--jobs 1` for a single process or `--jobs N` to set a specific worker count. Progress is atomically saved every 10,000 completed pairs to `<output>.checkpoint.json`, and an interrupted build automatically resumes from a compatible checkpoint. Compatibility includes the ordered Pokémon IDs and names, scoring constants, checkpoint format, and neighbor limit. A stale or malformed checkpoint is reported and ignored. Use `--no-resume` to deliberately restart the pair calculation, `--checkpoint` to choose another transient checkpoint path, or `--checkpoint-every N` to change the save interval. The checkpoint is removed only after the final CSV has been written successfully; checkpoint and generated feature-cache JSON files are ignored by Git.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers pure scoring and guess transitions, image processing and similarity generation, distractor rank bands, the FastAPI round flow, and leaderboard identity, persistence, ranking, tie, outage, and logout behavior.

## Runtime architecture

- `server/app.py` — FastAPI app, validated/cached packaged data, per-browser in-memory sessions, round commands, leaderboard APIs, and image endpoints.
- `server/leaderboard.py` — transactional SQLite round-score storage and deterministic top-10 ranking.
- `frontend/src/` — Round, Result, and Setup error screens with phone/iPad portrait layouts.
- `src/` — reusable domain, data, image, and similarity modules.
- `data/` — packaged Pokémon metadata, masks, artwork, and similarity index.

Session state is process-local and leaderboard state is SQLite-backed. Running multiple backend workers would require a shared session store and database; the default command intentionally uses one worker.

Set `POKEGAME_DATA_DIR` to test or run against an alternate packaged data directory.

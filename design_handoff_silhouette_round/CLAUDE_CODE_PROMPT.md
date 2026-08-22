# Prompt for Claude Code

Copy everything below the line into Claude Code, from the root of the `pokegame` repo, with this
handoff folder placed inside it (e.g. `./design_handoff_silhouette_round/`).

---

I'm rebuilding the presentation layer of this project. The domain logic and packaged data already
exist and are correct — I do not want them rewritten.

Read these three things first, in this order:

1. `design_handoff_silhouette_round/FUNCTIONAL_REBUILD_SPEC.md` — the functional spec. **Authoritative
   for behaviour.** It was written for exactly this rebuild; section 2 deliberately leaves presentation
   open and section 18 gives the implementation order.
2. `design_handoff_silhouette_round/README.md` — the design spec. **Authoritative for layout**: the
   square-stage rule, the iPhone/iPad step-up table, all four screens element by element with exact
   colors, type, sizes and copy, animation timings, and the state shape.
3. `design_handoff_silhouette_round/Silhouette Screen.dc.html` — the working prototype of the screen.
   Open it in a browser if anything in the README is ambiguous; the numbers in the README were taken
   from this file. It is a **design reference, not code to copy** — do not port its HTML/JS structure.

`design_handoff_silhouette_round/screenshots/` has the rendered screens at both sizes.
`Silhouette Game.dc.html` is only the review harness (two device frames + a screen switcher); ignore it
as an implementation target.

## What to build

The player-facing UI for a 30-second silhouette round, at iPhone (402pt) and iPad portrait (834pt),
covering four screens: **Start**, **Round**, **Result**, **Setup error**.

## Constraints

- **Reuse the existing domain code.** `points_for_attempt()` and `record_guess()` from `src/game.py`,
  `get_distractor_ids()` from `src/distractors.py`, and the loaders in `src/ui_data.py` stay as they
  are. Do not reimplement scoring, distractor selection, or data validation. Do not run PokéAPI calls
  or OpenCV pair comparisons at runtime (spec §16).
- **Preserve every behavioural contract in the spec.** In particular: absolute deadline that
  re-renders cannot extend (§13.3), 3/2/1/0 points by attempt (§12.3), wrong answers removed without
  touching the clock (§12.1), correct answers advancing on the same deadline (§12.2), guesses rejected
  after expiry (§13.3), targets not repeating until the pool is exhausted (§13.2), stable answer order
  per question (§11.1), and an actionable setup error instead of an exception for each failure in §15.
- **Follow the README's layout rule exactly.** The mask is square, so the stage is
  `aspect-ratio: 1`, centered, and absorbs all leftover height capped by column width. One column at
  both sizes. Do not introduce a two-column portrait layout, do not stretch the answer rows to fill
  height, and do not leave empty filler bands above or below the content — all three were tried in
  design and rejected.
- **Zero border radius anywhere, 2px rules, labels flush left, Archivo only.** Take colors, type and
  spacing from the token list in the README. Red is used sparingly (primary action, wrong answer,
  session best); correct answers fill ink, not red. Imagery is grayscale, never tinted.
- Streak, the sound/haptics toggle, and the reveal animation are **new scope beyond the spec** — build
  them, but the streak must never affect points.

## Framework choice

Decide and tell me which you're doing before you write code:

- **Option A — stay on Streamlit.** Lowest risk, reuses everything, but the rerun model fights the
  timer and the collapse animation. If you pick this, replace `app.py` entirely (the spec says it may
  be replaced) and keep the domain modules untouched.
- **Option B — FastAPI + a small React/Svelte frontend.** Wrap the existing `src/` modules in
  endpoints, serve the packaged masks and artwork, and build the screens as components. Better fit for
  the 4×/s timer, the row-collapse animation and the reveal sweep.

Recommend one, say why in two sentences, then proceed.

## Order of work

1. Confirm the packaged data loads and the existing tests pass (`python -m unittest discover -s tests -v`).
2. Stand up the chosen runtime with the domain modules wired in, no UI yet.
3. Build the Round screen at iPhone size first — timer, square stage, stats, feedback, answer rows —
   and get the removal/advance/expiry behaviour right before styling anything else.
4. Add the iPad breakpoint using the step-up table. Verify the stage stays square and flush at both
   sizes with no filler bands.
5. Add Start, Result and Setup error.
6. Wire real assets: masks at `data/silhouettes/{id:03d}.png` during play, grayscale artwork at
   `data/pokemon/artwork/{id}.png` on the result screen. Cache decoded images (§16).
7. Port the integration test in §17.2 to the new runtime — four choices, removal, second-guess scoring
   two points, advancement to a different target, unchanged deadline, expiry rejecting late guesses,
   replay. Replace the Streamlit-specific assertions if the framework changed.
8. Run the full suite and tell me what's covered and what isn't.

## Definition of done

Spec §22, plus: both breakpoints match the README's measurements, no console errors, and the four
screens are reachable and correct.

Ask me before adding anything not in these documents — especially a give-up action or an in-round
difficulty picker, both of which were deliberately left out.

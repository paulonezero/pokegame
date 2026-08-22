# Handoff: Silhouette Round — mobile + iPad UI

## Overview

This bundle is the **presentation layer** for the 30-second silhouette guessing game. The functional
behaviour is already specified in `FUNCTIONAL_REBUILD_SPEC.md` (included here, unchanged) and largely
already implemented in `src/` (`game.py`, `distractors.py`, `ui_data.py`, …). **That spec is
authoritative for behaviour. This README is authoritative for layout.** Where they overlap, the spec
wins on rules and this README wins on arrangement.

Section 2 of the spec deliberately left presentation open, and section 10 of its implementation order
says "build the new UX around the required capabilities in section 14". This is that UX.

## About the design files

`Silhouette Game.dc.html` and `Silhouette Screen.dc.html` are **design references written in HTML** —
a working prototype of the intended look and behaviour, not production code to copy. The job is to
**recreate them in the target environment**. Because the domain code is Python, the two realistic
targets are:

- **Keep Streamlit** — port the layout with `st.columns`, `st.container`, and a small CSS block. The
  square stage and reserved answer block are the parts that need care; everything else maps directly.
- **Add a JS frontend** (React/Svelte) over a small FastAPI wrapper around the existing `src/`
  modules. This is the better fit for the timer and the collapse animation, which Streamlit's rerun
  model fights.

Either way: reuse `points_for_attempt()` and `record_guess()` unchanged. Do not reimplement scoring.

`Silhouette Screen.dc.html` is the file to port — it holds the whole screen at both sizes, driven by a
`device` prop (`"phone" | "tablet"`). `Silhouette Game.dc.html` is only the review harness (two device
frames, the screen switcher, the annotation column); it has no counterpart in the shipped app.

## Fidelity

**High-fidelity.** Colors, type, spacing, rules and touch-target sizes are final and taken from the
Modernist design system tokens listed below. Recreate them exactly. The only intentional placeholders:

- **Silhouette shapes** are procedurally generated abstract blobs. Replace with the real masks at
  `data/silhouettes/{id:03d}.png`.
- **Names** (Aluvia, Bracken, Cindril, Dovemoss, Emberkin, Fennow, Glimmer, Harrowe, Ilexa, Juneberry,
  Kestrelle, Lumen) are neutral stand-ins. Replace with the metadata names.
- **The "artwork" on the result screen** is the same blob in mid-grey. Replace with the real artwork
  from `data/pokemon/artwork/{id}.png`, rendered grayscale.

---

## The layout rule (read this first)

**The mask is square (256×256), so the stage is square.** One column at every size:

```
timer row          fixed height, full width
stage              aspect-ratio: 1, centered, flex: 1, min-height: 0, max-width: 100%
stats row          content height, full width
feedback strip     content height, full width
answer block       content height, full width, min-height reserved for 4 rows
```

The control group (stats + feedback + answers) is content-height and sits flush to the bottom of the
screen. The stage absorbs **all** remaining height, capped by the column width. Consequences:

- iPhone (402pt) → stage ≈ **366pt square**
- iPad portrait (834pt) → stage ≈ **462pt square**
- The iPad's extra height goes into the mask. It must **not** go into taller buttons and must **not**
  become empty filler above or below the content. Both were tried and rejected.
- Do not use a two-column (stage beside controls) arrangement in portrait. It cannot hold a square
  stage at 834pt without leaving dead space at the sides. It *is* the right arrangement for **iPad
  landscape** if that gets built later — there the spare room is horizontal.

The silhouette inside the stage is sized as a **percentage of the stage (66% × 66%)**, so it scales
with the breakpoint automatically rather than at fixed pixel sizes.

**What steps up at the iPad breakpoint** (`device === "tablet"`):

| | iPhone | iPad |
|---|---|---|
| Screen padding | 56 / 18 / 34 | 34 / 40 / 40 |
| Column gap | 12 | 16 |
| Timer bar height | 10 | 14 |
| Timer label | 18 | 24 |
| Sound button | 34 × 34 | 40 × 40 |
| Stat label / value | 9.5 / 24 | 11 / 34 |
| Stat cell padding | 8 × 10 | 12 × 14 |
| Feedback strip | 32 min-h, 12.5 text | 40 min-h, 14.5 text |
| Answer row height | 56 | 74 |
| Answer row label | 17 | 22 |
| Answer row left pad | 18 | 24 |
| Points cell width | 52 | 68 |
| Answer row gap | 8 | 10 |
| Primary button | 58 min-h, 18 text | 66 min-h, 21 text |
| Secondary button | 48 min-h, 14 text | 54 min-h, 16 text |
| Display heading | 40 | 60 |

Below 380pt wide, drop the points cell from the answer rows rather than shrinking the label.

---

## Screens

There are four. All four live in one component, switched on a single `screen` value.

### 1. Start (pre-round)

**Purpose:** explain the scoring in three lines and let the player begin. The round deadline must not
start before this button is pressed (spec §13.1).

**Layout:** single column, vertically centered, `gap: 18` (phone) / `26` (iPad), max-width 620 on iPad.

| Element | Spec |
|---|---|
| Hero mark | 132pt (176 iPad) square, `--color-neutral-200` fill, 2px `--color-text` border, containing a 78pt (104) `--color-text` blob |
| Heading | "Name<br>the shape" — Archivo 800, 40pt (60), line-height .96, letter-spacing -.03em, **uppercase** |
| Lead | 15pt (18), opacity .78, `text-wrap: pretty` — "One silhouette, four names, thirty seconds. Wrong guesses drop out of the list and cost you points — the clock never stops." |
| Scoring list | 3 rows, 9px vertical padding, separated by 1px `--color-neutral-300`, opened by a 2px `--color-divider` rule and closed by a 2px one. Numeral in `--color-accent`, Archivo 800, 15pt (19), fixed 22pt (28) column. Rows: "3 / First guess right", "2 / Second guess", "1 / Third guess — the last one is free, and worth nothing" |
| Start button | `.btn .btn-primary`, full width, 58pt (66) min-height, label **flush left** at 18pt (24) padding, uppercase |
| Session best | Row, `space-between`, 12pt, uppercase, letter-spacing .06em, opacity .6 — "Best this session" / "{n} pts" |

### 2. Round (active question)

**Purpose:** the game. Every capability in spec §14 is reachable here.

**Timer row** — `display: flex; align-items: center; gap: 12`
- Track: `flex: 1`, 10pt (14) tall, `--color-neutral-300`, no radius
- Fill: width = `remaining / total`, `--color-text`, switching to `--color-accent` at ≤10s. Transition `width .24s linear, background .4s ease`
- Label: `ceil(remaining)` + "s", Archivo 800, 18pt (24), right-aligned, min-width 42 (56), also turns accent under 10s
- Sound/haptics toggle: 34 (40) square, 2px `--color-text` border. On = ink fill, `--color-bg` glyph; off = transparent with `--color-neutral-600` glyph and a slash. Lucide `volume-2` / `volume-x`

**Stage** — square, 2px `--color-text` border, `--color-neutral-200` ground, `overflow: hidden`
- Mask centered at 66% × 66%, `--color-text`
- Top-left caption: "Q{n}", 9.5pt, uppercase, letter-spacing .11em, opacity .5
- Top-right caption: difficulty + rank band, same style — e.g. "expert · ranks 1–5". Bands: easy 15–40, normal 5–20, hard 2–10, expert 1–5 (spec §10)
- On a correct guess: mask fills `--color-neutral-500` (stands for the grayscale artwork) with a `popIn .3s` scale-up, a white `sweep .6s` gradient wipe travels bottom→top, and a full-width ink banner appears at the stage bottom with the name in Archivo 800, 14pt (18), uppercase

**Stats row** — three equal cells inside a 2px top and 2px bottom `--color-text` rule, divided by 1px `--color-neutral-300`. Labels 9.5pt (11) uppercase letter-spacing .11em opacity .6; values Archivo 800 24pt (34) letter-spacing -.02em. Cells: **Score** (`score`), **Named** (`correct_count`), **Streak**. The streak cell has no right divider and its background transitions to `--color-accent-200` while streak > 1.

**Feedback strip** — one line, 32pt (40) min-height, 12px (16) horizontal padding, 12.5pt (14.5) semibold
- Idle: `--color-neutral-200` ground, `--color-neutral-600` text, "Pick the name that fits the shape."
- Wrong: `--color-accent-200` ground, `--color-accent-800` text, "Not {name} — removed. {n} pts left."
- Correct: `--color-text` ground, `--color-bg` text, "Correct — {name} · +{n}" (or "· no points left" at 0)

**Answer rows** — 4 rows, `gap: 8` (10). The container reserves `4 × rowHeight + gaps` as `min-height`
so a removal never moves the stage.
- Row: full width, 56pt (74) min-height, 2px `--color-text` border, transparent ground, label Archivo 800 17pt (22) **flush left** at 18pt (24), zero radius
- Points cell: right end, full row height, 52pt (68) wide, `--color-neutral-200`, Archivo 600 13pt (15) in `--color-neutral-700`, showing the points the *next* correct guess is worth
- Wrong: row flips to `--color-accent` ground with `--color-bg` label, shakes for 380ms
  (`translateX` 0 → -7 → 6 → -4 → 3 → 0), then collapses over 260ms (`max-height` → 0, `opacity` → 0,
  `margin-top` → -8/-10) and is dropped from the list
- Correct: row fills `--color-text` with `--color-bg` label, points cell goes `rgba(255,255,255,.16)`,
  badge shows "+{n}". Holds ~780ms, then the next question loads

### 3. Result (time expired)

**Purpose:** spec §13.4 summary.

| Element | Spec |
|---|---|
| Kicker | "Time's up", 10.5pt, uppercase, letter-spacing .12em, `--color-accent-700`, under a 2px ink rule |
| Score | "{n} PTS", Archivo 800, 40pt (60), uppercase, letter-spacing -.03em |
| Sub | "{n} shapes named in 30 seconds." (singular "One shape…" at 1) |
| Artwork block | 104pt (140) square, 2px ink border, `--color-neutral-200`, holding the final shape at 76pt (100) in `--color-neutral-500`; beside it "Last one was" / name in Archivo 800 26pt (34) / "Full artwork, revealed at expiry". Bounded top and bottom by 1px `--color-neutral-300` rules |
| Three cells | Closed by a 2px ink rule. **Named**, **Best streak**, and **Session best** — the last one filled `--color-accent` with `--color-bg` text |
| Actions | Primary "Play again", secondary "Back to start", both full width, labels flush left, pushed to the bottom with `margin-top: auto` |

If artwork is missing, still render the summary (spec §15) — show the empty bordered box.

### 4. Setup error

**Purpose:** every `SetupError` in spec §15 renders here rather than throwing.

56pt `--color-accent` square with a `--color-bg` alert glyph · heading "Data isn't ready" (Archivo 800,
40pt, uppercase) · the specific failure with the exact expected path, the path marked by a
`--color-neutral-200` fill and a 2px ink underline · a fix block bounded by 2px ink rules top and
bottom containing the label "Fix", the command (`python scripts/build_similarity.py` etc.) and one
line of consequence · a secondary "Retry" button.

Each failure listed in §15 gets its own copy — missing metadata, invalid JSON, fewer than four
records, missing/malformed similarity CSV, missing columns, target with fewer than three distractors,
missing or empty mask.

---

## Interactions & behaviour

All of this is already specified in `FUNCTIONAL_REBUILD_SPEC.md`; this is only the presentation-side
contract.

| Trigger | Behaviour |
|---|---|
| Start / Play again | New round: deadline = now + 30s, score and count to zero, targets reshuffled, question 1 built (spec §13.1) |
| Tick | Every ~240ms, recompute `remaining = deadline - now`. The prototype ticks 4×/s for a smooth bar; the spec's 1×/s minimum (§13.3) is satisfied either way. **Never** decrement a counter |
| Answer tap | If `now >= deadline`, end the round instead of scoring (§13.3). If already revealed or already removed, ignore |
| Wrong answer | `record_guess()` → shake, collapse, remove; attempt counter up; streak reset to 0; deadline untouched (§12.1) |
| Correct answer | `record_guess()` → points via `points_for_attempt()` (3/2/1/0), reveal, count up, streak up **only if first attempt**, then advance after 780ms on the same deadline (§12.2) |
| Deadline reached | Round completes, submissions stop, final target revealed, session best updated (§13.4) |
| Sound toggle | Presentation-only state. Wire to the Vibration API and short sample playback |

Animations: `shakeX .38s ease`, `popIn .3s ease`, `riseIn .3s ease` (screen entry), `sweep .6s ease`
(reveal wipe), row collapse `max-height/opacity/margin .26s/.22s/.26s ease`, timer
`width .24s linear`.

## State

Round state, matching the spec's suggested contract in §13:

```
screen        "start" | "play" | "result" | "error"
deadline      absolute timestamp — the single source of truth for time
timeLeft      derived, display only, clamped at 0
score, found  int
qNum          question number
attempt       1-based attempt within the current question
answers       ordered list of 4 ids (stable per question — persist or seed per §11.1)
removed       ids removed this question
reveal        bool — target shown, input locked
feedback      { kind: "ok" | "no", text }
streak, bestStreak, best
sound         bool
```

`streak` / `bestStreak` are **new product scope**, not in the spec. They are derived client-side from
consecutive first-attempt correct answers and must never affect points.

## Design tokens (Modernist)

Taken from the bound design system's `styles.css`. Use the variables, never the literals.

```
--color-bg          #f3f2f2      --color-neutral-200  #eae7e7
--color-surface     #eae9e9      --color-neutral-300  #d7d3d3
--color-text        #201e1d      --color-neutral-500  #9b9797
--color-accent      #ec3013      --color-neutral-600  #7d7979
--color-divider     rgba(#201e1d, .40)   --color-neutral-700  #605d5d
--color-accent-200  #ffe0d9      --color-neutral-900  #2d2b2b
--color-accent-700  #ae1800
--color-accent-800  #7c1405

--font-heading / --font-body   Archivo (400 / 600 / 800)
--space-1..8        4 · 8 · 12 · 16 · 24 · 32
--radius-sm/md/lg   0 · 0 · 0        ← zero radius everywhere, deliberately
```

House rules that matter here: **no rounded corners anywhere**, 2px rules do the organising, **button
and row labels are flush left, never centered**, accent red is used sparingly (primary action, wrong
answer, session best, small emphasis) — correct answers fill ink instead, and imagery is grayscale,
never tinted.

Interaction states come from the system: hover tints and pressed states from the accent ramp,
`:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px }`, disabled at 45%
opacity. Do not restyle them per screen.

Icons: Lucide.

## Assets

- **Silhouettes** — `data/silhouettes/{id:03d}.png`, 256×256 grayscale, already packaged. Cache the
  decoded masks (spec §16).
- **Artwork** — `data/pokemon/artwork/{id}.png`, result screen only, render grayscale.
- **Icons** — Lucide `volume-2`, `volume-x`, `alert-circle`. Not vendored here.
- **Font** — Archivo from Google Fonts, weights 400/600/800.
- No other assets. Nothing in the design needs an image that isn't already in the repo.

## Not built here

- **No give-up action.** Spec §13.4 notes `gave_up` exists in the domain helper but the product does
  not expose it. Still optional scope.
- **No in-round difficulty picker.** Difficulty is a build-time value (`expert`) surfaced only as the
  stage's rank-band caption.
- **iPad landscape and split view** reuse the iPad rule as-is. If you want a proper landscape layout,
  that is where stage-beside-controls belongs.
- **Accessibility** is specified only as far as visible focus, 44pt+ targets (56/74pt rows) and
  non-color-dependent feedback (copy changes, not just tint). Screen-reader announcement of removals
  and of the remaining time still needs designing.

## Files in this bundle

| File | What it is |
|---|---|
| `CLAUDE_CODE_PROMPT.md` | The prompt to paste into Claude Code to build this |
| `FUNCTIONAL_REBUILD_SPEC.md` | The functional spec, unchanged. Authoritative for behaviour |
| `Silhouette Screen.dc.html` | **The design to port.** The whole screen at both sizes, `device` prop switches the breakpoint |
| `Silhouette Game.dc.html` | Review harness: both device frames, screen switcher, annotation column. Not part of the app |
| `ios-frame.jsx` | iPhone bezel used by the harness. Not part of the app |
| `README.md` | This document. Authoritative for layout |
| `screenshots/` | Rendered screens at both sizes: start, round, wrong-answer-removed (iPhone), result, setup error |

To view the prototype: open `Silhouette Game.dc.html` in a browser. The four buttons at the top switch
both frames between Start, Round, Result and Error. Both frames run their own live 30-second round.

# Ekstraklasa Fantasy Optimizer (free, local, open-source)

An ILP-based squad optimizer for LOTTO Fantasy Ekstraklasa, in the
spirit of [open-fpl-solver](https://github.com/solioanalytics/open-fpl-solver)
. No paid APIs, no hosting cost —
everything runs on your laptop with `pandas` + `PuLP` (which bundles the
free CBC solver).

## 0. Project layout

```
main.py                    entry point — run/single/horizon modes
src/                        the solver itself (ILP core, config, settings)
  config.py                 all squad rules + the official scoring table
  projections.py            xPts model (Poisson clean sheets/goals conceded)
  solver.py, multi_week.py  single-gameweek and multi-week ILP
  data_loader.py, export.py, settings.py
pipeline/                   scripts that turn raw exports into player_pool.csv
  build_fixtures.py         raw FBref fixtures -> data/fixtures.csv (canonical)
  build_player_pool.py      the real weekly build — writes data/players_pool.csv
  build_vamps_pool.py       alternate source — reshapes VAMPS' own xPts export
  fbref_stats.py            shared FBref-parsing helpers used by the above
  parse_statsultra.py       StatsUltra club-strength + real match-odds parser
data/                        clean, ready-to-use data (fixtures.csv, club_strength.csv, ...)
  raw/                       exactly-as-downloaded exports (FBref, StatsUltra, status)
exports/                     solver output CSVs land here when you run main.py
settings.json                your actual weekly settings (edit this one)
settings.vamps.json          same, but points at the VAMPS projection source
settings.example.json        template — copy, don't edit in place
find_player_ids.py           look up player_id by name for initial_squad
```

## 1. Setup (one-time)

```bash
git clone <this repo>
cd ekstraklasa-optimizer
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Optional but recommended: `pip install highspy` for the HiGHS solver, which
is noticeably faster than CBC on the multi-week horizon problem. The code
auto-detects it — no config changes needed.

## 2. Feed it data

Edit `data/players_example.csv` (or point `--data` at your own file). It
ships with **324 players across all 18 real 2026/27 Ekstraklasa clubs**
(the 15 clubs that stayed up from 2025/26, plus Wisła Kraków, Śląsk
Wrocław, and Wieczysta Kraków, promoted from I liga) so you can run the
solver immediately. **Player names in the sample file are illustrative
placeholders, not real squads** — real prices and current rosters aren't
published anywhere free to scrape reliably, so the clubs are accurate but
the individual names are generic. Swap in real names/prices from the
official site or your own scouting once you're ready.

Columns:

| column | meaning |
|---|---|
| `player_id` | any unique integer |
| `name`, `club`, `position` (GK/DEF/MID/FWD) | |
| `price` | in millions, same unit as the 30 budget |
| `xpts_gw1`, `xpts_gw2`, ... | your projected points for each upcoming gameweek |
| `status` | `ok` / `doubt` / `out` — `out` players are auto-excluded |

**Google Sheets instead of CSV:** File → Share → Publish to web → choose
the sheet → CSV. Pass that URL straight as `--data <url>` — `pandas.read_csv`
reads URLs natively, no API key involved.

If you don't have projections yet, `src/projections.py` implements the
**official scoring rules exactly** (transcribed from
https://fantasy.ekstraklasa.org/page/howto, fetched July 2026) — goals,
assists, "LOTTO assists", clean sheets, penalty saves, appearance points,
match-win bonus, cards, missed penalties, own goals, and more, each scaled
by simple per-90/per-game rate estimates you supply. See the module
docstring for the full list of inputs. All point values live in
`src/config.py` as named constants (`GOAL_PTS`, `ASSIST_PTS`, etc.) so
they're easy to audit against the rules page if it changes.

## 3. Run it

Single gameweek:
```bash
python main.py single --data data/players_example.csv --gw xpts_gw1
```

5-week horizon (jointly optimized, not week-by-week greedy):
```bash
python main.py horizon --data data/players_example.csv --weeks 5
```

Play a Wildcard on week 3 of the horizon (free unlimited transfers that week):
```bash
python main.py horizon --weeks 5 --wildcard 3
```

Both print a readable squad/points summary to the console and write a CSV
to `exports/`. Errors (bad CSV columns, infeasible budget, missing
gameweek columns, etc.) print a plain-English explanation instead of a
stack trace.

## 4. How the solver works

`src/solver.py` — one ILP per gameweek:
- binary `squad[player]`, `lineup[player]`, `captain[player]`
- exact position counts (2/5/5/3), budget, 3-per-club, valid formation
  (GK 1, DEF 3-5, MID 3-5, FWD 1-3 — this exact combination of ranges
  reproduces the 7 formations the official rules allow: 3-4-3, 3-5-2,
  4-3-3, 4-4-2, 4-5-1, 5-3-2, 5-4-1, and no others)
- one captain who must start and scores double
- bench order is decided **after** solving (highest xPts outfield player
  first, reserve GK last) since it doesn't change the objective — see the
  simplification note in the code regarding the official autosub logic

`src/multi_week.py` — one joint ILP across the whole horizon:
- same per-week constraints, plus `transfer_in`/`transfer_out` variables
  linking week *w* to week *w-1*
- `hits[w] >= transfers_used[w] - 2` (2 free transfers/week, per the
  official rules), and the objective subtracts `3 * hits[w]`
- pass `wildcard_weeks={3}` (or `--wildcard 3` on the CLI) to make specific
  weeks transfer-free
- future weeks are discounted by `config.DEFAULT_DECAY` (0.9 by default)
  so near-term certainty is weighted above distant, less certain projections

**Known simplifications** (documented so you know what's missing, not
hidden):
- sell price == buy price (no price-rise/fall tracking)
- transfer banking (`config.BANK_TRANSFERS`) is present but only partially
  linearised — leave it `False` (the default) unless you finish that piece
- the goals-conceded penalty (rule "k": -1 per goal conceded, excluding the
  first) and clean sheets (rule "d") are both derived from one Poisson
  `expected_goals_against` per club/gameweek in `projections.py` — clean
  sheet = `exp(-lambda)`, expected penalty = `lambda - (1 - exp(-lambda))`,
  the exact expectation under that Poisson assumption (see
  `expected_conceded_penalty_pts()`'s docstring). The Poisson-arrivals
  assumption itself is standard but still a simplification of the real,
  match-specific goals distribution
- of the official chips, only **Wildcard** is wired into the ILP. Ekstra
  Transfer, Kapitanów Dwóch, Ławka Punktuje, and Joker are documented with
  their exact rules in `src/config.py` and `src/multi_week.py`'s docstring,
  including what each would take to implement, but aren't enforced yet

## 4b. Club strength + real fixture odds (StatsUltra)

`pipeline/parse_statsultra.py` turns a hand-saved StatsUltra snapshot into
the two files `pipeline/build_player_pool.py` consumes:

```bash
python pipeline/parse_statsultra.py --gw 1
```

- **Input:** paste StatsUltra's "Club Strength Ratings" table into
  `data/raw/statsultra_club_strength_raw.csv` (columns: `club, world_rank,
  team_strength, offence, defence`) and its "Upcoming Match Predictions"
  table into `data/raw/statsultra_next_round_raw.csv` (columns:
  `date, home_team, home_strength, home_win_pct, draw_pct, away_win_pct,
  away_team, away_strength, top_game`). Both are already filled in with the
  round fetched July 2026 as a working example.
- **Output:** `data/club_strength.csv` (feeds the ASM/DWM attack/defense
  multipliers in `build_player_pool.py`) and `data/fixtures_statsultra_probs.csv`
  (real win/draw probabilities, one row per team per fixture, tagged with
  the gameweek number you pass via `--gw`).
- For any `(team, gw)` with a real StatsUltra prediction, `build_player_pool.py`
  uses that win probability directly. For gameweeks StatsUltra hasn't
  published yet (it only ever shows the *next* round), it falls back to
  `estimate_win_prob()` — a linear model of `team_strength` difference,
  hand-calibrated against StatsUltra's own 9-fixture round (worst-case fit
  error: 0.6 percentage points; see the docstring in `parse_statsultra.py`).
- **Re-run weekly:** each week, copy StatsUltra's fresh table over the two
  raw CSVs and re-run `parse_statsultra.py` with that round's gameweek number.

Known limitation carried over from before: FBref stats are joined to your
player list by surname, with club as a tiebreaker for repeats (see
`pipeline/fbref_stats.py`'s `load_fbref_players()`). A player FBref has no
record for at all (backup keepers, youth players, summer signings from
outside the top 5 leagues) falls back to a position-average per-90 rate, so
their xPts is driven mostly by clean-sheet/win-probability/appearance terms
until you add a manual prior for them (see `MANUAL_PRIORS` in
`build_player_pool.py`).

## 4c. Clean sheets & goals conceded: a proper Poisson model

Rules (d) clean sheet and (k) goals conceded both depend on the same
underlying number — how many goals a club is expected to concede in a
match — so the model now computes that number **once** per club/gameweek
(`expected_goals_against`, a Poisson lambda combining the opponent's attack
rating, this club's own defense rating, and home advantage) and derives
both scoring rules from it exactly, in `src/projections.py`:

```
clean_sheet_prob        = exp(-lambda)                    # P(0 goals conceded)
expected_conceded_penalty = lambda - (1 - exp(-lambda))   # E[max(0, goals - 1)]
```

Previously these were two separately-tuned approximations (a flat
`0.30 * multiplier` clean-sheet guess and a `max(0, avg_conceded - 1)`
linear penalty) that could disagree with each other for the same team. Pass
your own `expected_goals_against` into `estimate_xpts()` for any custom
projections you write by hand — or the older `clean_sheet_prob` /
`goals_conceded_per_game` keyword arguments still work standalone if you'd
rather not model a full Poisson lambda.

## 4d. Weekly workflow: player status export + fixtures

Each week you'll get two fresh downloads:

1. **The player status export** (`id, name, team, position, price,
   overall_status, description, expectedEndDate, return_gameweek, ...`) —
   the source of truth for names/prices/status. Save it to
   `data/raw/player_status_export.csv` (overwrite last week's copy).
2. **The FBref fixtures export**, extended as far ahead as you can get it
   (5, 8, however many rounds are published). Save it to
   `data/raw/fixtures_8gw_raw.csv`.

Then run all three steps in order:

```bash
python pipeline/build_fixtures.py       # raw fixture export -> data/fixtures.csv
python pipeline/build_player_pool.py    # -> data/players_pool.csv
python main.py run --settings settings.json
```

**`pipeline/build_fixtures.py`** turns the raw FBref export into the
canonical `data/fixtures.csv` (`team, gw, opponent, is_home, date,
kickoff_local, status, notes`). If a match gets postponed or moved, add one
entry to the `RESCHEDULES` dict at the top of that file — don't hand-edit
`data/fixtures.csv` directly, since it's regenerated from the raw export
every run. Two statuses matter to the model:
- **`rescheduled`** — new date/kickoff, but it still counts as that
  gameweek's fixture for both clubs (e.g. Gameweek 2's Korona Kielce v
  Górnik Zabrze, moved to 2026-08-01 18:15 local — still Matchweek 2).
- **`postponed`** — no new date yet, so both clubs get a genuine blank
  gameweek (0 xpts, 0 expected minutes) rather than a guessed projection,
  since there's nothing on the pitch that round.

**`pipeline/build_player_pool.py`** writes `data/players_pool.csv`
(`player_id, name, club, position, price, status, xmins,
xpts_gw1..xpts_gwN`) — this is what `settings.json`'s `projection_file`
points at. It:

- uses the status export's `id` as `player_id` going forward (stable week
  to week), matched by name+club against FBref per-90 stats + the I liga
  fallback for the 3 promoted clubs (same logic `fbref_stats.py` uses)
- turns `overall_status` into expected minutes: **EXP is left untouched**
  (FBref's own Starts/MP rate, which lands nailed players — especially
  goalkeepers — around 80-90 expected minutes on its own); **MAY** ->
  ~45 expected minutes; **NES** -> ~20; **OUT** -> 0 xpts for every
  gameweek before `return_gameweek`, ramping back up (partial the return
  week, full from the week after) — see the module docstring for the exact
  numbers and the reasoning for not hard-excluding players who'll be back
  inside the horizon
- reuses `data/club_strength.csv` team ratings (not gameweek-specific) plus
  `data/fixtures.csv` to compute each club's `expected_goals_against` for
  every gameweek, using real StatsUltra odds for whichever round is
  closest and the strength-difference fallback for everything beyond that

`settings.json` (not `settings.example.json`, which stays a template) is
your actual weekly settings file — edit `initial_squad` (your current 15
player IDs from the status export), `itb`, `use_wildcard`, etc. each week
and re-run. It already points at `data/players_pool.csv` with an 8-week
horizon. `chip_plan` is a free-form, not-yet-enforced field for jotting
your season-long chip plan (e.g. `{"10": "wildcard"}`) next to the rest of
your settings — only Wildcard actually changes the solve, and only once
its gameweek is also listed in `use_wildcard`.

## 4e. Two projection sources — this project's own model, or VAMPS' own xPts

You now have two interchangeable `projection_file` options, both in the
exact same CSV shape (`player_id, name, club, position, price, status,
xmins, xpts_gw1..xpts_gw8`), so switching is a one-line change:

| File | Built by | Notes |
|---|---|---|
| `data/players_pool.csv` | `pipeline/build_player_pool.py` | this project's own FBref+fixtures+Poisson model (see 4b-4d) |
| `data/players_pool_vamps.csv` | `pipeline/build_vamps_pool.py` | VAMPS' own published xPts, reshaped into the same schema |

**To use VAMPS' numbers instead:**
```bash
python pipeline/build_vamps_pool.py     # data/raw/vamps_xpts_export.csv -> data/players_pool_vamps.csv
python main.py run --settings settings.vamps.json
```
`settings.vamps.json` is a ready-made copy of `settings.json` pointing at
`data/players_pool_vamps.csv` — edit its `initial_squad`/`itb`/`ft` the same
way you edit `settings.json`, they're independent files so you can keep
both squads' settings side by side, or just overwrite `settings.json`'s
`projection_file` field if you only ever want to use one at a time.

**Weekly refresh:** save a fresh copy of VAMPS' own export to
`data/raw/vamps_xpts_export.csv` (same weekly habit as the other raw files
in 4d) and re-run `build_vamps_pool.py`. It's a straight column
reshape — VAMPS' `1_Pts..8_Pts` are used as-is, not re-derived — the only
thing added is a rough status-based `xmins` estimate (VAMPS doesn't publish
minutes) purely so `xmin_lb` filtering still works; see the module
docstring if you rely on that filter heavily with this source.

Nothing else in the solver (`src/`) needs to know which source you're
using — it only ever reads whatever CSV `projection_file` points at.

## 5. Suggested next steps

1. Replace the synthetic sample data with real prices + your own
   projections (even a spreadsheet you fill in by hand each week is fine
   to start).
2. Add price-change tracking once you have a source for it, so
   multi-week transfers account for sell price properly.
3. Wire in the remaining chips as extra per-week ILP variables/constraints
   (see the docstring in `multi_week.py` for exactly what each needs).
4. If useful, port the World Cup project's UI/API layer (it's a fork of
   open-fpl-solver) as a thin frontend over this same `src/` — the ILP
   core here is deliberately UI-agnostic so that's a drop-in later step.

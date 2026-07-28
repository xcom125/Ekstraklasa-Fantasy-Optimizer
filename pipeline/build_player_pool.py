"""
Rebuilds the master player pool from the weekly player-status export and
computes xpts_gw1..xpts_gw8.

INPUTS
------
1. data/raw/player_status_export.csv
   (id, name, team, position, price, overall_status, description,
   expectedEndDate, return_gameweek, 1_Pts..8_Pts, total_Pts). This is the
   source of truth for id/name/club/position/price/status — the `id` column
   here becomes player_id going forward (stable week to week, so this is
   what future weeks' exports should be matched against, by id AND as a
   name+club fallback in case the id scheme in a later export ever shifts).
2. data/fixtures.csv — the canonical GW1-8 fixture list (built from the raw
   FBref export by pipeline/build_fixtures.py; run that first if fixtures
   change). Includes a `status` column: "scheduled" fixtures are used as
   normal, "rescheduled" ones use their updated date/kickoff but still count
   as that gameweek's match, and "postponed" ones (no new date yet) are
   treated as a blank gameweek for both clubs — see build_fixtures.py.
3. FBref per-90 stats (data/raw/Standard_Stats.csv, *_Miscellaneous_Stats.xls,
   *_Goal_Keeper_stats.xls) + the hand-curated I liga fallback for the 3
   promoted clubs — reused as-is from pipeline/fbref_stats.py.
4. data/club_strength.csv — team_strength ratings, reused for every one of
   the 8 gameweeks (it's a static rating, not fixture-specific) to estimate
   win_prob via the same strength-difference model parse_statsultra.py uses
   as its fallback for rounds StatsUltra hasn't published yet. GW1 uses the
   REAL published probabilities from data/fixtures_statsultra_probs.csv where
   available; GW2-8 all use the fallback, since StatsUltra only ever
   publishes the next round.

CLEAN SHEET / GOALS CONCEDED MODEL
------------------------------------
Each club/gameweek gets one number, expected_goals_against (a Poisson
lambda), derived from the opponent's attack rating vs. this club's defense
rating (+ home advantage). That single number now drives BOTH rule (d)
clean sheets (P(0 conceded) = exp(-lambda)) and rule (k) goals-conceded
deductions (E[max(0, N-1)] = lambda - (1 - exp(-lambda)), the exact Poisson
expectation) via src/projections.py — instead of the two being tuned
separately against different ad hoc formulas, which is what this script did
before.

overall_status -> minutes model
--------------------------------
This is the ONLY thing this script adjusts on top of the normal FBref-driven
per-90 rates. Per your instructions:

    EXP  untouched — start_prob/bench_prob come straight from FBref's own
         Starts/MP season rates, same as fbref_stats.py always did.
         Nailed regulars land ~80-90 expected minutes this way already
         (goalkeepers especially, since they're almost never subbed);
         nothing here overrides that.
    MAY  ("may start") -> start_prob=0.45, bench_prob=0.25  (~45 xmins)
    NES  ("not expected to start") -> start_prob=0.15, bench_prob=0.35 (~20 xmins)
    OUT  -> 0 xpts / 0 xmins for every gameweek BEFORE return_gameweek.
         From return_gameweek: MAY-level minutes for the return week itself
         (rust/managed comeback), then EXP-level (their normal FBref rate)
         from the following week — a simplification, but a better one than
         either hard-excluding a player who'll be back inside the horizon or
         pretending they're at full speed the week they return.
         If return_gameweek is blank (unknown return), treated as OUT for
         the whole horizon and given status='out' so the solver excludes
         them entirely rather than guessing at a return week.

Every other status keeps status='ok' — including MAY/NES/returning-OUT
players — because the ILP should decide whether a rotation risk is still
worth their price based on the (now reduced) xpts, not have them removed
from consideration altogether. Hard-exclusion (`status='out'`) is reserved
for players with genuinely no known return date.

OUTPUT: data/players_pool.csv — player_id, name, club, position,
price, status, xmins, xpts_gw1..xpts_gw8. Same shape the solver already
expects (see src/data_loader.py), just with 8 weeks instead of 5.
"""

from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.projections import estimate_xpts
from pipeline.fbref_stats import (
    load_fbref_players, build_iliga_lookup, ascii_fold, PROMOTED_CLUBS,
)
from pipeline.parse_statsultra import estimate_win_prob
from pipeline.team_names import canonical_team

STATUS_PATH = Path("data/raw/player_status_export.csv")
FIXTURES_PATH = Path("data/fixtures.csv")
CLUB_STRENGTH_PATH = Path("data/club_strength.csv")
STATSULTRA_PROBS_PATH = Path("data/fixtures_statsultra_probs.csv")
OUTPUT_PATH = Path("data/players_pool.csv")

# ---- SofaScore master-pool ingestion branch (pipeline swap) ----
MASTER_POOL_PATH = Path("data/raw/player_pool.csv")
MASTER_STATS_PATH = Path("data/raw/ekstraklasa_master_players.csv")
MASTER_LAST_SEASON_STATS_PATH = Path("data/raw/ekstraklasa_master_players_last_season.csv")

N_GAMEWEEKS = 8
POS_MAP = {"GKP": "GK", "GK": "GK", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}
MULTIPLIER_CLIP = (0.5, 1.8)
HOME_ADVANTAGE = 1.08
LEAGUE_AVG_GOALS_PER_GAME = 1.3  # baseline expected goals for a side of average attack vs. average defense

STATUS_MINUTES = {
    "MAY": (0.45, 0.25),
    "NES": (0.15, 0.35),
}

# Hand-researched priors for specific players where FBref's last-season rate
# is known to be stale or missing the current picture (injury comeback, a
# preseason showing that pre-dates any FBref sample, etc.). Every entry is
# sourced below. Combined with the FBref/I-liga/fallback rate via max() —
# same as the EXP minutes floor — so this can only raise a projection, never
# lower one; if you have more of these, add them the same way.
MANUAL_PRIORS = {
    ("Mikael Ishak", "Lech Poznań"): {
        "start_prob_floor": 0.95,
        "note": "Lech's undisputed #1 striker — 16 league goals in 2025/26 (FotMob), "
                "started the 2026 Super Cup vs Górnik Zabrze and is in the CL qualifying "
                "squad; no rotation threat at his position.",
    },
    ("Patrik Wålemark", "Lech Poznań"): {
        "start_prob_floor": 0.88,
        "goals_per90_floor": 0.35,
        "assists_per90_floor": 0.15,
        "note": "FBref's last-season sample is almost entirely from before his July 2025 "
                "surgery, so it understates him. Made his 2026/27 competitive return in the "
                "CL qualifier vs Aarhus and scored twice in 66 minutes (UEFA.com) — too small "
                "a sample to take at face value, but enough to justify a real floor rather "
                "than the ~0 goals/90 his injury-hit FBref season alone would imply.",
    },
}


def _apply_manual_priors(name, club, start_p, g90, a90):
    p = MANUAL_PRIORS.get((name, club))
    if not p:
        return start_p, g90, a90
    start_p = max(start_p, p.get("start_prob_floor", 0.0))
    g90 = max(g90, p.get("goals_per90_floor", 0.0))
    a90 = max(a90, p.get("assists_per90_floor", 0.0))
    return start_p, g90, a90


def _clip(x: float) -> float:
    lo, hi = MULTIPLIER_CLIP
    return max(lo, min(hi, x))


def _xmins(start_prob: float, bench_prob: float) -> float:
    return round(min(90.0, max(0.0, start_prob * 90 + bench_prob * 20)), 1)


def load_status(path: Path = STATUS_PATH) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"'{path}' not found.")
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df = df[df["id"].notna()].copy()
    df["id"] = df["id"].astype(int)
    df["position"] = df["position"].astype(str).str.upper().map(POS_MAP)
    df["overall_status"] = df["overall_status"].astype(str).str.strip().str.upper()
    df["return_gameweek"] = pd.to_numeric(df["return_gameweek"], errors="coerce")
    if df["id"].duplicated().any():
        dupes = df.loc[df["id"].duplicated(), "id"].tolist()
        raise SystemExit(f"Duplicate id(s) in status export: {dupes}")
    return df


def load_fixtures_by_club_gw(path: Path = FIXTURES_PATH) -> dict:
    """Reads the canonical data/fixtures.csv (run pipeline/build_fixtures.py
    first if it's stale). A "postponed" fixture is left OUT of the dict on
    purpose, so club/gw lookups for it fall through to the no-fixture
    default below (start_p=bench_p=0 for that gameweek) — there's nothing on
    the pitch that round to project points for."""
    if not path.exists():
        raise SystemExit(f"'{path}' not found. Run pipeline/build_fixtures.py first.")
    raw = pd.read_csv(path)

    out: dict[tuple[str, int], dict] = {}
    postponed: set[tuple[str, int]] = set()
    for _, row in raw.iterrows():
        gw = int(row["gw"])
        if row["status"] == "postponed":
            postponed.add((row["team"], gw))
            continue
        out[(row["team"], gw)] = {"opponent": row["opponent"], "is_home": bool(row["is_home"])}
    max_gw = raw["gw"].max()
    print(f"Loaded fixtures for {max_gw} gameweek(s) from '{path}'"
          f"{f' ({len(postponed)} postponed team-fixture entries -> blank gameweek)' if postponed else ''}.")
    return out, postponed


def load_strength(path: Path = CLUB_STRENGTH_PATH) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.attrs["league_avg_attack"] = df["attack_rating"].mean()
    df.attrs["league_avg_defense"] = df["defense_rating"].mean()
    return df


def load_real_probs(path: Path = STATSULTRA_PROBS_PATH) -> dict:
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return {(r["team"], int(r["gw"])): r["win_prob"] for _, r in df.iterrows()}


def build():
    status_df = load_status()
    fixtures, postponed = load_fixtures_by_club_gw()
    strength = load_strength()
    real_probs = load_real_probs()
    fbref = load_fbref_players()
    iliga = build_iliga_lookup()

    strength_flat = strength.set_index("club")["team_strength"].to_dict()
    strength_by_club = strength.set_index("club")[["attack_rating", "defense_rating"]].to_dict("index")
    league_avg_attack = strength.attrs["league_avg_attack"]
    league_avg_defense = strength.attrs["league_avg_defense"]

    fbref["surname_key"] = fbref["surname"].apply(ascii_fold)
    fbref_by_surname_club = {(r["surname_key"], r["club"]): r for _, r in fbref.iterrows()}
    fbref_by_surname: dict[str, list] = {}
    for _, r in fbref.iterrows():
        fbref_by_surname.setdefault(r["surname_key"], []).append(r)
    pos_avg = fbref.groupby("primary_pos")[["goals_per90", "assists_per90"]].mean().to_dict("index")

    # GK penalty-save rate and saves/game are both single-season per-90 rates
    # off small samples (a handful of penalties faced, a season of shots
    # against) — noisy enough that one busy/lucky season can make a keeper
    # look like the best player in the league regardless of position. Shrink
    # each keeper's rate 50% toward the league-wide GK average so a real
    # signal (a genuinely busy or sharp keeper) still comes through, but an
    # outlier season doesn't dominate the projection the way it was before.
    gk_rows = fbref[fbref["primary_pos"] == "GK"]
    league_avg_pen_save = gk_rows["penalty_save_prob"].mean() if len(gk_rows) else 0.3
    league_avg_saves = gk_rows["saves_per_game"].mean() if len(gk_rows) else 2.5
    SHRINK = 0.5

    def _shrink_gk_rate(raw: float, league_avg: float) -> float:
        return SHRINK * league_avg + (1 - SHRINK) * raw

    n_fbref, n_iliga, n_fallback = 0, 0, 0
    unmatched_examples = []

    gw_cols = {gw: [] for gw in range(1, N_GAMEWEEKS + 1)}
    xmins_out, status_out = [], []

    for _, p in status_df.iterrows():
        full_name, club, pos = p["name"], p["team"], p["position"]
        vstatus = p["overall_status"]
        surname_key = ascii_fold(str(full_name).split()[-1])

        row = fbref_by_surname_club.get((surname_key, club))
        source = None
        if row is not None:
            source, n_fbref = "fbref", n_fbref + 1
        elif surname_key in iliga:
            source, n_iliga = "iliga", n_iliga + 1
        else:
            candidates = fbref_by_surname.get(surname_key)
            if candidates and len(candidates) == 1:
                row, source, n_fbref = candidates[0], "fbref_other_club", n_fbref + 1
            else:
                source, n_fallback = "fallback", n_fallback + 1
                if len(unmatched_examples) < 15:
                    unmatched_examples.append(f"{full_name} ({club}, {pos})")

        # base per-90 rates from FBref's historical rate (quality signal).
        # NOTE: penalty_save_prob is a PKsv/PKatt ratio over a handful of
        # attempts per season (often 1-3) -> wildly noisy (0%, 50%, 100% are
        # all common outcomes off tiny samples). Clipped to a sane ceiling so
        # one lucky/unlucky penalty doesn't swing a keeper's whole projection.
        if source in ("fbref", "fbref_other_club"):
            fbref_start, fbref_bench = row["start_prob"], row["bench_prob"]
            g90_base, a90_base = row["goals_per90"], row["assists_per90"]
            extra = dict(
                penalty_save_prob=_shrink_gk_rate(min(row["penalty_save_prob"], 0.35), league_avg_pen_save),
                saves_per_game=_shrink_gk_rate(row["saves_per_game"], league_avg_saves) if pos == "GK" else row["saves_per_game"],
                yellow_card_prob=row["yellow_card_prob"], red_card_prob=row["red_card_prob"],
                penalty_won_prob=row["penalty_won_prob"], own_goal_prob=row["own_goal_prob"],
                penalty_caused_prob=row["penalty_caused_prob"],
            )
        elif source == "iliga":
            info = iliga[surname_key]
            fbref_start, fbref_bench = 0.6, 0.15
            g90_base, a90_base = info.get("goals_per90", 0.0), info.get("assists_per90", 0.0)
            extra = {}
        else:
            avg = pos_avg.get(pos, {"goals_per90": 0.05, "assists_per90": 0.05})
            fbref_start, fbref_bench = 0.4, 0.2
            g90_base, a90_base = avg["goals_per90"], avg["assists_per90"]
            extra = {}

        # apply hand-researched priors (Ishak/Wålemark/etc.) — raise-only
        fbref_start, g90_base, a90_base = _apply_manual_priors(full_name, club, fbref_start, g90_base, a90_base)

        # EXP-status minutes: a FLOOR, not a pass-through of FBref's own
        # start_prob. FBref reflects LAST season, so it understates anyone
        # whose situation has since changed (returned from injury, new
        # signing, breakout preseason, etc.) — e.g. a winger who missed most
        # of 2025/26 recovering from surgery but is status-flagged EXP for
        # 2026/27 would otherwise be stuck on his injured season's low
        # minutes. Pure max() on both start_prob and bench_prob -> this can
        # only RAISE a projection, never lower one; EXP guarantees ~80-90
        # expected minutes this way (more for GKs, who are essentially never
        # subbed), while a player FBref already rates higher keeps that
        # higher number untouched.
        exp_floor_start = 0.95 if pos == "GK" else 0.88
        exp_start = max(fbref_start, exp_floor_start)
        exp_bench = max(fbref_bench, 0.05)

        # decide whether this is a hard exclusion (unknown-return OUT)
        hard_out = vstatus == "OUT" and pd.isna(p["return_gameweek"])
        status_out.append("out" if hard_out else "ok")

        club_strength = strength_by_club.get(club)

        for gw in range(1, N_GAMEWEEKS + 1):
            # --- a postponed fixture with no new date yet = a genuine blank
            # gameweek for this club: 0 minutes, 0 xpts, regardless of status ---
            if (club, gw) in postponed:
                gw_cols[gw].append(0.0)
                if gw == 1:
                    xmins_out.append(0.0)
                continue

            # --- status -> this gameweek's start/bench prob ---
            if vstatus == "EXP" or source != "fbref" and vstatus not in ("MAY", "NES", "OUT"):
                start_p, bench_p = exp_start, exp_bench
            elif vstatus in ("MAY", "NES"):
                start_p, bench_p = STATUS_MINUTES[vstatus]
            elif vstatus == "OUT":
                ret = p["return_gameweek"]
                if pd.isna(ret) or gw < ret:
                    start_p, bench_p = 0.0, 0.0
                elif gw == ret:
                    start_p, bench_p = STATUS_MINUTES["MAY"]
                else:
                    start_p, bench_p = exp_start, exp_bench
            else:
                start_p, bench_p = exp_start, exp_bench

            # --- fixture difficulty for this club/gw ---
            # own_defense_factor: a genuinely weak defense shouldn't get the
            # same flat clean-sheet baseline as a strong one just because its
            # keeper faces (and saves) a lot of shots — that combination was
            # inflating "shambles defense" goalkeepers, who got full credit
            # for save volume AND an unadjusted clean-sheet chance.
            own_defense_factor = 1.0
            if club_strength:
                own_defense_factor = _clip(club_strength["defense_rating"] / league_avg_defense) \
                    if league_avg_defense else 1.0
            asm = dwm = 1.0
            win_prob = 0.33
            fx = fixtures.get((club, gw))
            if fx and club_strength:
                opp = strength_by_club.get(fx["opponent"])
                if opp:
                    asm = _clip(league_avg_defense / opp["defense_rating"]) if opp["defense_rating"] else 1.0
                    dwm = _clip(league_avg_attack / opp["attack_rating"]) if opp["attack_rating"] else 1.0
                    dwm *= own_defense_factor
                if fx["is_home"]:
                    asm *= HOME_ADVANTAGE
                    dwm *= HOME_ADVANTAGE
                real = real_probs.get((club, gw))
                if real is not None:
                    win_prob = real
                elif opp and strength_flat.get(club) is not None and strength_flat.get(fx["opponent"]) is not None:
                    home_s = strength_flat[club] if fx["is_home"] else strength_flat[fx["opponent"]]
                    away_s = strength_flat[fx["opponent"]] if fx["is_home"] else strength_flat[club]
                    hw, draw, aw = estimate_win_prob(home_s, away_s)
                    win_prob = hw if fx["is_home"] else aw

            # expected_goals_against: this club's Poisson lambda for goals
            # conceded this match. dwm > 1 means a weaker-than-average
            # opponent attack (+ this club's own defense quality, + home
            # advantage) -> fewer expected goals against, and vice versa.
            # Passed straight into estimate_xpts(), which derives BOTH the
            # clean-sheet probability and the expected goals-conceded
            # deduction from this single number (see src/projections.py).
            expected_goals_against = _clip(LEAGUE_AVG_GOALS_PER_GAME / dwm) if dwm else LEAGUE_AVG_GOALS_PER_GAME

            pts = estimate_xpts(
                position=pos, start_prob=start_p, bench_prob=bench_p,
                goals_per90=g90_base * asm, assists_per90=a90_base * asm,
                expected_goals_against=expected_goals_against if pos in ("GK", "DEF", "MID") else None,
                win_prob=win_prob,
                **extra,
            )
            gw_cols[gw].append(round(max(0.0, pts), 2))

            if gw == 1:
                xmins_out.append(_xmins(start_p, bench_p))

    print(f"\nMatch summary: {n_fbref} via FBref, {n_iliga} via curated I liga data, "
          f"{n_fallback} via position-average fallback.")
    if unmatched_examples:
        print("Sample fallback cases (no FBref/I liga data found):")
        for e in unmatched_examples:
            print(f"    - {e}")

    out = pd.DataFrame({
        "player_id": status_df["id"].values,
        "name": status_df["name"].values,
        "club": status_df["team"].values,
        "position": status_df["position"].values,
        "price": status_df["price"].values,
        "status": status_out,
        "xmins": xmins_out,
    })
    for gw in range(1, N_GAMEWEEKS + 1):
        out[f"xpts_gw{gw}"] = gw_cols[gw]

    n_hard_out = (out["status"] == "out").sum()
    n_out_returning = ((status_df["overall_status"] == "OUT") & status_df["return_gameweek"].notna()).sum()
    n_may = (status_df["overall_status"] == "MAY").sum()
    n_nes = (status_df["overall_status"] == "NES").sum()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(out)} players (player_id = status export id) to {OUTPUT_PATH}")
    print(f"Status breakdown: {n_hard_out} hard-excluded (OUT, no known return), "
          f"{n_out_returning} OUT-but-returning-within-8-GW (xpts=0 until return_gameweek), "
          f"{n_may} MAY, {n_nes} NES, rest EXP.")
    xpts_all = out[[f"xpts_gw{gw}" for gw in range(1, N_GAMEWEEKS + 1)]].values.flatten()
    print(f"xPts range: {xpts_all.min():.2f} to {xpts_all.max():.2f}")
    return out



# =========================================================================
# NEW INGESTION BRANCH -- SofaScore master player pool + Stats Ultra
# =========================================================================
# Pipeline swap: replaces the old data/raw/player_status_export.csv +
# FBref-per-90 pipeline above with:
#   1. data/raw/player_pool.csv          -- master roster (565 players).
#      Source of truth for id/name/position/price/club. GKP -> GK for
#      solver.py compatibility (POS_MAP, same as the old branch).
#   2. data/raw/ekstraklasa_master_players.csv -- SofaScore per-player
#      expected stats for the CURRENT season (xG, xA, xGI, xGI_per_90,
#      minutes_played, key_passes, shots_total, shots_on_target). Primary
#      source of baseline player expected metrics -- no FBref stats are
#      pulled in this branch.
#   3. data/raw/ekstraklasa_master_players_last_season.csv -- the SAME
#      SofaScore stat shape, one full season (2025/26) prior. Joined on
#      `player_id` (the SofaScore id -- note this file names that column
#      `player_id` where the current-season file calls it `ekstraklasa_id`'s
#      sibling `player_id`; both are the SofaScore id, see
#      load_master_stats_last_season()). Used to season the zero/low-minute
#      current-season rates with a real track record instead of leaning on
#      the position average alone -- see HISTORICAL BLENDING below. A
#      player's CLUB from last season is irrelevant here (transfers happen)
#      -- only their own per-90 history travels with them via the id join;
#      this season's fixture-strength engine (ASM/DWM below) already
#      accounts for a new club's fixture list, so no separate "step up/down
#      a division" adjustment is applied to the base rate itself.
#   4. data/fixtures.csv -- unchanged, still the sole source of the
#      match calendar (built from FBref by build_fixtures.py).
#   5. data/club_strength.csv / data/fixtures_statsultra_probs.csv --
#      unchanged in shape, now populated from the Stats Ultra "Club
#      Strength & Next Match Predictions" export via
#      pipeline/parse_statsultra_md.py + pipeline/parse_statsultra.py.
#      Same Poisson xGA engine (ASM/DWM multipliers, HOME_ADVANTAGE) as
#      the old branch below -- reused as-is, not rebuilt.
#
# projections.py / solver.py / multi_week.py / src/config.py are untouched:
# this branch ends at the exact same output shape (player_id, name, club,
# position, price, status, xmins, xpts_gw1..N) that data_loader.py already
# expects, and none of the official scoring constants in config.py change
# just because the stat SOURCE changed.
#
# HISTORICAL BLENDING (this season + last season + position average)
# ---------------------------------------------------------------------
# Three layers, each one a fallback for the one before it:
#   1. THIS season's observed per-90 rate (xg/minutes, xa/minutes) --
#      freshest signal, trusted in proportion to minutes seen
#      (w_cur = min(1, minutes_played / MASTER_MIN_SAMPLE_MINUTES)).
#   2. LAST season's observed per-90 rate for that same player (by
#      SofaScore id) -- a full season's sample, so far more reliable per
#      minute than this season's ~1 gameweek, but discounted with
#      LAST_SEASON_RATE_DECAY since a year old (form, role, or fitness may
#      have changed) and trusted in proportion to ITS OWN minutes
#      (w_last = min(1, last_minutes / LAST_SEASON_MIN_SAMPLE_MINUTES) *
#      LAST_SEASON_RATE_DECAY).
#   3. This season's POSITION AVERAGE (same fallback as before) -- used
#      whenever there's no last-season row for a player at all (new to the
#      league, or simply absent from the export).
# Combined as prior = w_last * last_rate + (1 - w_last) * pos_avg, then
# final = w_cur * this_season_rate + (1 - w_cur) * prior. This means:
#   - An established player with a full sample this season: ~entirely
#     this-season rate, same as before this update.
#   - The ~325 zero-minute-THIS-season players: w_cur=0, so they fall
#     straight to `prior` -- if we have their 2025/26 track record, THEIR
#     OWN history now replaces the flat position-average guess; if we
#     don't (genuinely new/promoted/foreign signing), position average is
#     still the fallback, exactly as before this update.
# Expected minutes for zero-minute players get the same treatment: a
# player with real last-season appearances gets expected_minutes derived
# from their own last-season minutes-per-appearance (discounted, since
# current fitness/favor is unknown) rather than the generic position-wide
# ZERO_MIN_DISCOUNT used when there's no history at all -- see
# ZERO_MIN_DISCOUNT_WITH_HISTORY below.
MASTER_MIN_SAMPLE_MINUTES = 270.0        # ~3 full matches for full confidence weight (this season)
LAST_SEASON_MIN_SAMPLE_MINUTES = 900.0   # ~10 matches for full confidence weight (last season)
LAST_SEASON_RATE_DECAY = 0.65            # last season's signal is capped below full trust, however
                                          # many minutes it covers -- it's a year old
ZERO_MIN_DISCOUNT = 0.35                 # expected-minutes discount, NO last-season history at all
ZERO_MIN_DISCOUNT_WITH_HISTORY = 0.55    # expected-minutes discount, HAS a last-season track record
                                          # (more confidence than a total unknown, still shy of presuming
                                          # they've already reclaimed their old role)
STARTER_MINUTES_FLOOR = 85.0             # played (almost) the full match -> nailed-starter floor


def load_master_pool(path: Path = MASTER_POOL_PATH) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"'{path}' not found.")
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df["pos"] = df["pos"].astype(str).str.upper().map(POS_MAP)
    if df["pos"].isna().any():
        bad = df.loc[df["pos"].isna(), "web_name"].tolist()
        raise SystemExit(f"'{path}': unmapped position code(s) for: {bad}")
    df["team_name"] = df["team_name"].apply(lambda t: canonical_team(t))
    if df["ekstraklasa_id"].duplicated().any():
        dupes = df.loc[df["ekstraklasa_id"].duplicated(), "ekstraklasa_id"].tolist()
        raise SystemExit(f"Duplicate ekstraklasa_id in '{path}': {dupes}")
    return df


def load_master_stats(path: Path = MASTER_STATS_PATH) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"'{path}' not found.")
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    stat_cols = ["appearances", "minutes_played", "goals", "xg", "assists", "xa",
                 "xGI", "xGI_per_90", "key_passes", "shots_total", "shots_on_target"]
    for c in stat_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df[["ekstraklasa_id"] + stat_cols]


def load_master_stats_last_season(path: Path = MASTER_LAST_SEASON_STATS_PATH) -> pd.DataFrame | None:
    """Optional. Returns None (rather than raising) if the file isn't
    present, so this branch still runs fine without historical data -- it
    just falls back to the position-average-only behaviour from before this
    file existed.

    Returns the raw per-player rows (id, name, stat columns) rather than a
    ready-to-merge table, because matching against the current pool needs
    THREE tiers, not a single id join -- see match_last_season() below for
    why: the ~325 zero-minute-this-season players are exactly the ones
    missing a SofaScore `player_id` in the current export (SofaScore only
    assigns an id once it's tracked a match for that player), so an id-only
    join would silently recover history for 0 of them -- precisely the
    group this feature is for.
    """
    if not path.exists():
        print(f"Note: no last-season stats file at '{path}' -- "
              f"skipping historical blending, using position-average fallback only.")
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    stat_cols = ["appearances", "minutes_played", "goals", "xg", "assists", "xa"]
    for c in stat_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
    if df["player_id"].notna().any():
        dup_ids = df.loc[df["player_id"].notna() & df["player_id"].duplicated(), "player_id"].tolist()
        if dup_ids:
            raise SystemExit(f"Duplicate player_id(s) in '{path}': {dup_ids}")
    return df[["player_id", "player_name"] + stat_cols]


def match_last_season(pool: pd.DataFrame, last_season: pd.DataFrame) -> pd.DataFrame:
    """Three-tier match of this season's roster (`pool`, from player_pool.csv)
    against last season's per-player stats, returning one row per pool
    player with ls_appearances/ls_minutes_played/ls_goals/ls_xg/ls_assists/
    ls_xa (0.0 where no match was found at any tier) plus ls_match_tier for
    the summary counts printed in build_from_master_pool().

    Tier 1 -- player_id (SofaScore id): exact, unambiguous, but only
        reaches the ~240 players SofaScore has already assigned an id to
        this season -- i.e. never the zero-minute group.
    Tier 2 -- full name, ascii-folded (diacritics/casing ignored):
        verified 0 duplicate full names on either side of this dataset, so
        this is as safe as an id match and is what recovers most of the
        zero-minute group's history, transfers included (team isn't part
        of the key, on purpose -- a player's last-season club is often not
        their current one).
    Tier 3 -- surname only, ascii-folded, but ONLY when that surname is
        unique across last season's whole player list (~510/540 are) --
        a deliberately conservative last resort for name-formatting
        mismatches (missing middle name, different transliteration, etc.)
        that would otherwise miss tier 2's exact-string match.
    """
    stat_cols = ["appearances", "minutes_played", "goals", "xg", "assists", "xa"]

    ls = last_season.copy()
    ls["_id"] = pd.to_numeric(ls["player_id"], errors="coerce")
    ls["_name_key"] = ls["player_name"].apply(ascii_fold)
    ls["_surname_key"] = ls["player_name"].apply(lambda n: ascii_fold(str(n).split()[-1]))

    by_id = {int(r["_id"]): r for _, r in ls.iterrows() if pd.notna(r["_id"])}
    by_name = {r["_name_key"]: r for _, r in ls.iterrows()}
    surname_counts = ls["_surname_key"].value_counts()
    by_unique_surname = {r["_surname_key"]: r for _, r in ls.iterrows()
                          if surname_counts[r["_surname_key"]] == 1}

    rows, tiers = [], []
    for _, p in pool.iterrows():
        pid = p["player_id"]
        name_key = ascii_fold(p["player_name"])
        surname_key = ascii_fold(str(p["player_name"]).split()[-1])

        match, tier = None, "none"
        if pd.notna(pid) and int(pid) in by_id:
            match, tier = by_id[int(pid)], "id"
        elif name_key in by_name:
            match, tier = by_name[name_key], "name"
        elif surname_key in by_unique_surname:
            match, tier = by_unique_surname[surname_key], "surname"

        if match is not None:
            rows.append({f"ls_{c}": match[c] for c in stat_cols})
        else:
            rows.append({f"ls_{c}": 0.0 for c in stat_cols})
        tiers.append(tier)

    out = pd.DataFrame(rows, index=pool.index)
    out["ls_match_tier"] = tiers
    return out


def build_from_master_pool(
    pool_path: Path = MASTER_POOL_PATH,
    stats_path: Path = MASTER_STATS_PATH,
    last_season_stats_path: Path = MASTER_LAST_SEASON_STATS_PATH,
    fixtures_path: Path = FIXTURES_PATH,
    strength_path: Path = CLUB_STRENGTH_PATH,
    statsultra_probs_path: Path = STATSULTRA_PROBS_PATH,
    output_path: Path = OUTPUT_PATH,
) -> pd.DataFrame:
    pool = load_master_pool(pool_path)
    stats = load_master_stats(stats_path)
    last_season = load_master_stats_last_season(last_season_stats_path)
    fixtures, postponed = load_fixtures_by_club_gw(fixtures_path)
    strength = load_strength(strength_path)
    real_probs = load_real_probs(statsultra_probs_path)

    df = pool.merge(stats, on="ekstraklasa_id", how="left", validate="one_to_one")
    for c in ["appearances", "minutes_played", "goals", "xg", "assists", "xa"]:
        df[c] = df[c].fillna(0.0)

    if last_season is not None:
        ls_matched = match_last_season(pool, last_season)
        df = pd.concat([df, ls_matched], axis=1)
        tier_counts = df["ls_match_tier"].value_counts().to_dict()
        n_with_history = (df["ls_match_tier"] != "none").sum()
        print(f"Last-season history matched for {n_with_history}/{len(df)} players "
              f"(by id: {tier_counts.get('id', 0)}, by full name: {tier_counts.get('name', 0)}, "
              f"by unique surname: {tier_counts.get('surname', 0)}).")
    else:
        for c in ["ls_appearances", "ls_minutes_played", "ls_goals", "ls_xg", "ls_assists", "ls_xa"]:
            df[c] = 0.0

    strength_flat = strength.set_index("club")["team_strength"].to_dict()
    strength_by_club = strength.set_index("club")[["attack_rating", "defense_rating"]].to_dict("index")
    league_avg_attack = strength.attrs["league_avg_attack"]
    league_avg_defense = strength.attrs["league_avg_defense"]

    # --- per-90 attacking rates ---
    df["xg_per90_raw"] = (df["xg"] / df["minutes_played"] * 90.0).where(df["minutes_played"] > 0)
    df["xa_per90_raw"] = (df["xa"] / df["minutes_played"] * 90.0).where(df["minutes_played"] > 0)
    df["ls_xg_per90_raw"] = (df["ls_xg"] / df["ls_minutes_played"] * 90.0).where(df["ls_minutes_played"] > 0)
    df["ls_xa_per90_raw"] = (df["ls_xa"] / df["ls_minutes_played"] * 90.0).where(df["ls_minutes_played"] > 0)
    df["ls_minutes_per_app"] = (df["ls_minutes_played"] / df["ls_appearances"]).where(df["ls_appearances"] > 0)

    played = df[df["minutes_played"] > 0]
    pos_avg_xg90 = played.groupby("pos")["xg_per90_raw"].mean().to_dict()
    pos_avg_xa90 = played.groupby("pos")["xa_per90_raw"].mean().to_dict()
    pos_avg_minutes = played.groupby("pos")["minutes_played"].mean().to_dict()
    # sane fallback if a position somehow has zero played-sample rows
    GLOBAL_XG90 = played["xg_per90_raw"].mean() if len(played) else 0.05
    GLOBAL_XA90 = played["xa_per90_raw"].mean() if len(played) else 0.05
    GLOBAL_MIN = played["minutes_played"].mean() if len(played) else 45.0

    n_played, n_imputed_with_history, n_imputed_no_history = 0, 0, 0

    def _row_rates(r):
        nonlocal n_played, n_imputed_with_history, n_imputed_no_history
        pos = r["pos"]
        avg_xg90 = pos_avg_xg90.get(pos, GLOBAL_XG90)
        avg_xa90 = pos_avg_xa90.get(pos, GLOBAL_XA90)
        avg_min = pos_avg_minutes.get(pos, GLOBAL_MIN)

        # --- historical prior: last season's rate (if any), else position average ---
        has_history = r["ls_minutes_played"] > 0
        if has_history:
            w_last = min(1.0, r["ls_minutes_played"] / LAST_SEASON_MIN_SAMPLE_MINUTES) * LAST_SEASON_RATE_DECAY
            prior_xg90 = w_last * r["ls_xg_per90_raw"] + (1 - w_last) * avg_xg90
            prior_xa90 = w_last * r["ls_xa_per90_raw"] + (1 - w_last) * avg_xa90
        else:
            prior_xg90, prior_xa90 = avg_xg90, avg_xa90

        if r["minutes_played"] > 0:
            n_played += 1
            w_cur = min(1.0, r["minutes_played"] / MASTER_MIN_SAMPLE_MINUTES)
            g90 = w_cur * r["xg_per90_raw"] + (1 - w_cur) * prior_xg90
            a90 = w_cur * r["xa_per90_raw"] + (1 - w_cur) * prior_xa90
            start_p = min(0.95, max(0.05, r["minutes_played"] / 90.0))
            bench_p = 0.10 if r["minutes_played"] >= 60 else 0.20
            if r["minutes_played"] >= STARTER_MINUTES_FLOOR:
                floor = 0.95 if pos == "GK" else 0.88
                start_p = max(start_p, floor)
        else:
            g90, a90 = prior_xg90, prior_xa90
            if has_history:
                n_imputed_with_history += 1
                # own historical minutes-per-appearance, discounted -- more
                # confidence than a total unknown, still short of presuming
                # they've already reclaimed last year's exact role.
                expected_minutes = ZERO_MIN_DISCOUNT_WITH_HISTORY * r["ls_minutes_per_app"]
            else:
                n_imputed_no_history += 1
                expected_minutes = ZERO_MIN_DISCOUNT * avg_min
            start_p = min(0.5, max(0.0, expected_minutes / 90.0))
            bench_p = 0.15

        return pd.Series({"g90": g90, "a90": a90, "start_p": start_p, "bench_p": bench_p})

    rates = df.apply(_row_rates, axis=1)
    df = pd.concat([df, rates], axis=1)

    gw_cols = {gw: [] for gw in range(1, N_GAMEWEEKS + 1)}
    xmins_out = []

    for _, r in df.iterrows():
        club, pos = r["team_name"], r["pos"]
        club_strength = strength_by_club.get(club)
        own_defense_factor = 1.0
        if club_strength and league_avg_defense:
            own_defense_factor = _clip(club_strength["defense_rating"] / league_avg_defense)

        for gw in range(1, N_GAMEWEEKS + 1):
            if (club, gw) in postponed:
                gw_cols[gw].append(0.0)
                if gw == 1:
                    xmins_out.append(0.0)
                continue

            start_p, bench_p = r["start_p"], r["bench_p"]

            asm = dwm = 1.0
            win_prob = 0.33
            fx = fixtures.get((club, gw))
            if fx and club_strength:
                opp = strength_by_club.get(fx["opponent"])
                if opp:
                    asm = _clip(league_avg_defense / opp["defense_rating"]) if opp["defense_rating"] else 1.0
                    dwm = _clip(league_avg_attack / opp["attack_rating"]) if opp["attack_rating"] else 1.0
                    dwm *= own_defense_factor
                if fx["is_home"]:
                    asm *= HOME_ADVANTAGE
                    dwm *= HOME_ADVANTAGE
                real = real_probs.get((club, gw))
                if real is not None:
                    win_prob = real
                elif opp and strength_flat.get(club) is not None and strength_flat.get(fx["opponent"]) is not None:
                    home_s = strength_flat[club] if fx["is_home"] else strength_flat[fx["opponent"]]
                    away_s = strength_flat[fx["opponent"]] if fx["is_home"] else strength_flat[club]
                    hw, draw, aw = estimate_win_prob(home_s, away_s)
                    win_prob = hw if fx["is_home"] else aw

            expected_goals_against = _clip(LEAGUE_AVG_GOALS_PER_GAME / dwm) if dwm else LEAGUE_AVG_GOALS_PER_GAME

            pts = estimate_xpts(
                position=pos, start_prob=start_p, bench_prob=bench_p,
                goals_per90=r["g90"] * asm, assists_per90=r["a90"] * asm,
                expected_goals_against=expected_goals_against if pos in ("GK", "DEF", "MID") else None,
                win_prob=win_prob,
            )
            gw_cols[gw].append(round(max(0.0, pts), 2))

            if gw == 1:
                xmins_out.append(_xmins(start_p, bench_p))

    out = pd.DataFrame({
        "player_id": df["ekstraklasa_id"].values,
        "name": df["player_name"].values,
        "club": df["team_name"].values,
        "position": df["pos"].values,
        "price": df["price"].values,
        "status": "ok",
        "xmins": xmins_out,
    })
    for gw in range(1, N_GAMEWEEKS + 1):
        out[f"xpts_gw{gw}"] = gw_cols[gw]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    print(f"\nMaster-pool branch: {n_played} players with recorded minutes this season, "
          f"{n_imputed_with_history} zero-minute players imputed from THEIR OWN last-season "
          f"history, {n_imputed_no_history} zero-minute players imputed via position-average "
          f"baseline rates (no last-season history found).")
    print(f"Wrote {len(out)} players (player_id = ekstraklasa_id) to {output_path}")
    xpts_all = out[[f"xpts_gw{gw}" for gw in range(1, N_GAMEWEEKS + 1)]].values.flatten()
    print(f"xPts range: {xpts_all.min():.2f} to {xpts_all.max():.2f}")
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Rebuild data/players_pool.csv from either ingestion branch.")
    parser.add_argument(
        "--source", choices=["master", "status"], default="master",
        help="'master' (default): SofaScore master player pool + Stats Ultra "
             "(data/raw/player_pool.csv + ekstraklasa_master_players.csv). "
             "'status': legacy data/raw/player_status_export.csv + FBref branch.")
    parser.add_argument(
        "--no-last-season", action="store_true",
        help="'master' branch only: skip data/raw/ekstraklasa_master_players_last_season.csv "
             "even if present, and fall back to position-average-only imputation "
             "(useful for comparing projections with/without historical blending).")
    args = parser.parse_args()
    if args.source == "master":
        build_from_master_pool(
            last_season_stats_path=Path("/nonexistent") if args.no_last_season
            else MASTER_LAST_SEASON_STATS_PATH,
        )
    else:
        build()

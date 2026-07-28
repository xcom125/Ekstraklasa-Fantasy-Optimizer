"""
Turns the StatsUltra "Club Strength Ratings and Upcoming Ekstraklasa Match
Predictions" export into the two clean CSVs the rest of the pipeline wants:

  1. data/club_strength.csv
     club, attack_rating, defense_rating, team_strength
     -> feeds pipeline/build_player_pool.py's ASM/DWM multipliers (unchanged
        logic, just a real data source now instead of a placeholder).

  2. data/fixtures_statsultra_probs.csv
     team, gw, opponent, is_home, win_prob, draw_prob, date
     -> real, model-implied win/draw probabilities for whichever round
        StatsUltra published (one row per team per fixture). build_player_pool.py
        uses these DIRECTLY for win_prob instead of deriving it from the
        attack/defense multiplier, whenever they're available for a given
        (team, gw).

WHERE THE NUMBERS COME FROM
----------------------------
Both inputs are hand-saved copies of the StatsUltra page (pasted as text,
not scraped live — StatsUltra has no free API), sitting in:
    data/raw/statsultra_club_strength_raw.csv
    data/raw/statsultra_next_round_raw.csv
Re-save these two files (same column layout) each week when you copy a
fresh StatsUltra snapshot; this script re-derives everything else.

ONLY ONE ROUND OF REAL FIXTURES
--------------------------------
StatsUltra's page only ever shows the *next* round's predictions, not an
8-gameweek-ahead schedule — so fixtures_statsultra_probs.csv only ever
covers one gameweek at a time (labelled gw=1, i.e. "the next round" in
whatever horizon you're solving). The opponent list for gw2+ comes from
data/fixtures.csv (see pipeline/build_fixtures.py) instead, and
build_player_pool.py falls back to estimate_win_prob() below (a plain
strength-difference model) for those rounds rather than real odds, since
StatsUltra hasn't published win/draw/loss splits for them yet.

FALLBACK WIN-PROBABILITY MODEL
--------------------------------
estimate_win_prob() is a simple linear fit calibrated against StatsUltra's
own 9-fixture round (see below) rather than an arbitrary guess:

    home_win_pct ~= 42.3 + 1.142 * (home_strength - away_strength)
    draw_pct     ~= 25.0 - 0.15  * abs(home_strength - away_strength)
    away_win_pct = 100 - home_win_pct - draw_pct

Fit by hand against the 9 real (diff, home_win_pct) pairs in the raw file
(max residual ~0.6 pct points across all 9 -- see git history / the module
docstring in earlier drafts for the fitting notebook if you want to redo
this with a larger sample later). Treat it as a placeholder that's *better
than a flat 33/33/33 guess*, not a substitute for real published odds.
Clipped to keep an extreme rating gap from producing a >90% single-outcome
probability.
"""

from __future__ import annotations
from pathlib import Path

import pandas as pd

RAW_STRENGTH_PATH = Path("data/raw/statsultra_club_strength_raw.csv")
RAW_FIXTURES_PATH = Path("data/raw/statsultra_next_round_raw.csv")
CLUB_STRENGTH_OUT = Path("data/club_strength.csv")
FIXTURES_PROBS_OUT = Path("data/fixtures_statsultra_probs.csv")

# Calibration constants for the fallback model -- see module docstring.
HOME_WIN_INTERCEPT = 42.3
HOME_WIN_SLOPE = 1.142
DRAW_BASE = 25.0
DRAW_SLOPE = 0.15
PROB_CLIP = (0.05, 0.90)


def build_club_strength(
    raw_path: Path = RAW_STRENGTH_PATH,
    out_path: Path = CLUB_STRENGTH_OUT,
) -> pd.DataFrame:
    if not raw_path.exists():
        raise SystemExit(
            f"'{raw_path}' not found. Paste the StatsUltra club-strength table into a CSV "
            f"there with columns: club, world_rank, team_strength, offence, defence."
        )
    raw = pd.read_csv(raw_path)
    required = {"club", "team_strength", "offence", "defence"}
    missing = required - set(raw.columns)
    if missing:
        raise SystemExit(f"'{raw_path}' is missing column(s): {missing}")

    out = pd.DataFrame({
        "club": raw["club"],
        "attack_rating": raw["offence"],
        "defense_rating": raw["defence"],
        "team_strength": raw["team_strength"],
    })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"Wrote {len(out)} clubs to {out_path}")
    return out


def _clip_pct(p: float) -> float:
    lo, hi = PROB_CLIP
    return max(lo, min(hi, p))


def estimate_win_prob(home_strength: float, away_strength: float) -> tuple[float, float, float]:
    """Fallback (home_win_prob, draw_prob, away_win_prob) for fixtures without
    a real published StatsUltra prediction. See module docstring for the fit.
    Returns probabilities as 0-1 floats that sum to 1.0."""
    diff = home_strength - away_strength
    home_pct = HOME_WIN_INTERCEPT + HOME_WIN_SLOPE * diff
    draw_pct = DRAW_BASE - DRAW_SLOPE * abs(diff)
    home_pct = max(5.0, min(90.0, home_pct))
    draw_pct = max(10.0, min(35.0, draw_pct))
    away_pct = 100.0 - home_pct - draw_pct
    if away_pct < 5.0:
        # squeeze draw down a little rather than let away go negative/tiny
        deficit = 5.0 - away_pct
        draw_pct -= deficit
        away_pct = 5.0
    total = home_pct + draw_pct + away_pct
    return (home_pct / total, draw_pct / total, away_pct / total)


def build_fixture_probs(
    raw_path: Path = RAW_FIXTURES_PATH,
    out_path: Path = FIXTURES_PROBS_OUT,
    gw: int = 1,
) -> pd.DataFrame:
    if not raw_path.exists():
        raise SystemExit(
            f"'{raw_path}' not found. Paste the StatsUltra upcoming-fixtures table into a CSV "
            f"there with columns: date, home_team, home_strength, home_win_pct, draw_pct, "
            f"away_win_pct, away_team, away_strength, top_game."
        )
    raw = pd.read_csv(raw_path)
    required = {"date", "home_team", "home_win_pct", "draw_pct", "away_win_pct", "away_team"}
    missing = required - set(raw.columns)
    if missing:
        raise SystemExit(f"'{raw_path}' is missing column(s): {missing}")

    rows = []
    for _, r in raw.iterrows():
        rows.append({
            "team": r["home_team"], "gw": gw, "opponent": r["away_team"],
            "is_home": True, "win_prob": round(r["home_win_pct"] / 100.0, 4),
            "draw_prob": round(r["draw_pct"] / 100.0, 4), "date": r["date"],
        })
        rows.append({
            "team": r["away_team"], "gw": gw, "opponent": r["home_team"],
            "is_home": False, "win_prob": round(r["away_win_pct"] / 100.0, 4),
            "draw_prob": round(r["draw_pct"] / 100.0, 4), "date": r["date"],
        })

    out = pd.DataFrame(rows).sort_values(["gw", "team"]).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"Wrote {len(out)} team-fixture rows (gw={gw}, {len(raw)} matches) to {out_path}")
    return out


def run(gw: int = 1) -> None:
    build_club_strength()
    build_fixture_probs(gw=gw)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Parse StatsUltra exports into club_strength.csv + fixtures_statsultra_probs.csv")
    parser.add_argument("--gw", type=int, default=1, help="Which gameweek number this StatsUltra round of fixtures corresponds to")
    args = parser.parse_args()
    run(gw=args.gw)

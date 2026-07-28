"""
Converts VAMPS' own xPts export (data/raw/vamps_xpts_export.csv) into the
same schema pipeline/build_player_pool.py produces, so it's a drop-in
alternative `projection_file` for settings.json — nothing else in the
solver needs to know which projection source you're using.

INPUT:  data/raw/vamps_xpts_export.csv
        (id, name, team, position, price, overall_status, description,
        expectedEndDate, return_gameweek, 1_Pts..8_Pts, total_Pts) — save a
        fresh copy here each week (overwrite last week's).
OUTPUT: data/players_pool_vamps.csv
        (player_id, name, club, position, price, status, xmins,
        xpts_gw1..xpts_gw8) — same shape as data/players_pool.csv.

This is a straight column rename/reshape, NOT a re-derivation — VAMPS'
1_Pts..8_Pts are used as-is (they already appear to build in status/return
handling: OUT players' columns are 0 up to their return_gameweek and ramp
up after, same idea our own model uses). The only thing this script adds:

  - `status`: 'out' (hard-excluded by the solver) for any player flagged
    OUT with no return_gameweek at all (unknown return -> nothing to
    project); everyone else is 'ok', including OUT-but-returning players,
    same convention build_player_pool.py uses — the ILP can weigh a
    rotation/injury risk against its (already-reduced) xpts rather than
    have it removed from consideration outright.
  - `xmins`: VAMPS doesn't publish expected minutes, so this is a rough
    status-based estimate (EXP~85, MAY~45, NES~20, OUT~0 or partial around
    return) purely so the solver's `xmin_lb` filter still has something to
    work with. It is NOT read from any VAMPS data — treat it as a coarse
    stand-in, not a real minutes model, if you rely on xmin_lb with this
    projection source.
"""

from __future__ import annotations
from pathlib import Path

import pandas as pd

INPUT_PATH = Path("data/raw/vamps_xpts_export.csv")
OUTPUT_PATH = Path("data/players_pool_vamps.csv")

POS_MAP = {"GKP": "GK", "GK": "GK", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}
N_GAMEWEEKS = 8

# Rough status -> expected-minutes stand-in (see module docstring — VAMPS
# doesn't publish minutes, this only exists so xmin_lb has something to filter on).
STATUS_XMINS = {"EXP": 85.0, "MAY": 45.0, "NES": 20.0}


def _xmins_for(status: str, return_gw: float) -> float:
    if status == "OUT":
        return 0.0 if pd.isna(return_gw) else 45.0  # partial credit once they're back in the picture
    return STATUS_XMINS.get(status, 60.0)


def build(input_path: Path = INPUT_PATH, output_path: Path = OUTPUT_PATH) -> pd.DataFrame:
    if not input_path.exists():
        raise SystemExit(f"'{input_path}' not found. Save this week's VAMPS xPts export there first.")
    df = pd.read_csv(input_path)
    df.columns = [c.strip() for c in df.columns]

    required = {"id", "name", "team", "position", "price", "overall_status", "return_gameweek"} \
        | {f"{gw}_Pts" for gw in range(1, N_GAMEWEEKS + 1)}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"'{input_path}' is missing column(s): {sorted(missing)}")

    df = df[df["id"].notna()].copy()
    df["id"] = df["id"].astype(int)
    if df["id"].duplicated().any():
        dupes = df.loc[df["id"].duplicated(), "id"].tolist()
        raise SystemExit(f"Duplicate id(s) in '{input_path}': {dupes}")

    df["overall_status"] = df["overall_status"].astype(str).str.strip().str.upper()
    df["return_gameweek"] = pd.to_numeric(df["return_gameweek"], errors="coerce")
    hard_out = (df["overall_status"] == "OUT") & df["return_gameweek"].isna()

    out = pd.DataFrame({
        "player_id": df["id"],
        "name": df["name"],
        "club": df["team"],
        "position": df["position"].astype(str).str.upper().map(POS_MAP).fillna(df["position"]),
        "price": df["price"],
        "status": ["out" if h else "ok" for h in hard_out],
        "xmins": [
            _xmins_for(s, r) for s, r in zip(df["overall_status"], df["return_gameweek"])
        ],
    })
    for gw in range(1, N_GAMEWEEKS + 1):
        out[f"xpts_gw{gw}"] = pd.to_numeric(df[f"{gw}_Pts"], errors="coerce").fillna(0.0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)

    n_out = int(hard_out.sum())
    xpts_all = out[[f"xpts_gw{gw}" for gw in range(1, N_GAMEWEEKS + 1)]].values.flatten()
    print(f"Wrote {len(out)} players to {output_path}")
    print(f"  {n_out} hard-excluded (OUT, no known return)")
    print(f"  xPts range: {xpts_all.min():.2f} to {xpts_all.max():.2f}")
    return out


if __name__ == "__main__":
    build()

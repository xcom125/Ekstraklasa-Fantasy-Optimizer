"""
Loads player data from a CSV (or a Google Sheet exported/published as CSV —
File > Share > Publish to web > CSV, then pass that URL straight to
load_players()). No paid API needed.

Expected columns (extra columns are ignored, so feel free to keep your own
notes/scouting columns alongside these):

    player_id, name, club, position, price, xpts_gw<N> (one per week), status

`status` lets you mark a player OUT for injury/suspension without deleting
the row: values "ok" / "doubt" / "out". "out" players are excluded from
selection automatically.
"""

from __future__ import annotations
import pandas as pd
from src import config


REQUIRED_COLUMNS = {"player_id", "name", "club", "position", "price"}
VALID_STATUSES = {"ok", "doubt", "out"}


def load_players(source: str) -> pd.DataFrame:
    """source can be a local path or a published Google Sheet CSV URL."""
    try:
        df = pd.read_csv(source)
    except FileNotFoundError:
        raise SystemExit(
            f"Couldn't find a players file at '{source}'. Check the path, or pass "
            f"--data <path-or-url> pointing at your CSV / published Google Sheet."
        )
    except pd.errors.EmptyDataError:
        raise SystemExit(f"'{source}' is empty — nothing to load.")

    df.columns = [c.strip() for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise SystemExit(
            f"'{source}' is missing required column(s): {sorted(missing)}. "
            f"Expected at least: {sorted(REQUIRED_COLUMNS)}."
        )

    if df["player_id"].duplicated().any():
        dupes = df.loc[df["player_id"].duplicated(), "player_id"].tolist()
        raise SystemExit(f"Duplicate player_id value(s) in '{source}': {dupes}. IDs must be unique.")

    df["position"] = df["position"].astype(str).str.upper().str.strip()
    valid_positions = set(config.SQUAD_POSITION_LIMITS)
    bad_positions = set(df["position"]) - valid_positions
    if bad_positions:
        raise SystemExit(
            f"Unknown position code(s) in '{source}': {sorted(bad_positions)}. "
            f"Expected one of {sorted(valid_positions)}."
        )

    if "status" not in df.columns:
        df["status"] = "ok"
    df["status"] = df["status"].fillna("ok").astype(str).str.lower().str.strip()
    bad_status = set(df["status"]) - VALID_STATUSES
    if bad_status:
        print(f"Warning: unrecognised status value(s) {sorted(bad_status)} in '{source}' "
              f"— treating as 'ok'. Expected one of {sorted(VALID_STATUSES)}.")
        df.loc[~df["status"].isin(VALID_STATUSES), "status"] = "ok"

    # Discover how many gameweek projection columns are present, e.g.
    # xpts_gw1..xpts_gw5. If only a single "xpts" column exists, treat it as gw1.
    gw_cols = sorted(
        [c for c in df.columns if c.lower().startswith("xpts_gw")],
        key=lambda c: int(c.lower().replace("xpts_gw", "")),
    )
    if not gw_cols and "xpts" in df.columns:
        df = df.rename(columns={"xpts": "xpts_gw1"})
        gw_cols = ["xpts_gw1"]
    if not gw_cols:
        raise SystemExit(
            f"No xpts_gw* (or xpts) projection column found in '{source}'. "
            f"Add at least one, or see src/projections.py to auto-generate one "
            f"from raw per-90 stats."
        )

    for c in gw_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    if df["price"].isna().any():
        bad_rows = df.loc[df["price"].isna(), "name"].tolist()
        raise SystemExit(f"Non-numeric or missing price for: {bad_rows}. Fix these rows in '{source}'.")

    df.attrs["gw_columns"] = gw_cols

    print(f"Loaded {len(df)} players from '{source}' "
          f"({len(gw_cols)} gameweek projection column(s): {', '.join(gw_cols)}).")
    n_out = (df["status"] == "out").sum()
    if n_out:
        print(f"  {n_out} player(s) marked 'out' will be excluded from selection.")

    return df


def available_players(df: pd.DataFrame, xmin_lb: float = 0.0) -> pd.DataFrame:
    """Drop players explicitly marked as OUT, and (if xmin_lb > 0 and an
    `xmins` column exists) anyone predicted to play fewer minutes than
    xmin_lb — the solver settings' way of avoiding heavy rotation risks
    even when their per-90 rates look good on paper."""
    out = df[df["status"] != "out"].copy()

    if xmin_lb > 0:
        if "xmins" not in out.columns:
            print(f"Warning: xmin_lb={xmin_lb} was set but no 'xmins' column exists in the data — "
                  f"ignoring xmin_lb. Run pipeline/fbref_stats.py to generate one.")
        else:
            before = len(out)
            out = out[out["xmins"] >= xmin_lb]
            n_excluded = before - len(out)
            if n_excluded:
                print(f"  {n_excluded} player(s) excluded for predicted minutes below xmin_lb={xmin_lb}.")

    out.attrs["gw_columns"] = df.attrs.get("gw_columns", [])
    return out

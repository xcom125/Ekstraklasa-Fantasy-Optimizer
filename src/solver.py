"""
Core single-gameweek squad optimizer.

Picks the 15-man squad + starting XI + captain that maximises expected
points for one gameweek, subject to:
  - 30M budget
  - 2 GK / 5 DEF / 5 MID / 3 FWD squad composition
  - max 3 players per club
  - valid formation for the starting XI (GK1, DEF3-5, MID3-5, FWD1-3)
  - captain scores double

Uses PuLP, which ships with the free CBC solver — no license, no API key,
runs entirely on your laptop. HiGHS is a drop-in swap if you install
`highspy` (see _pick_solver() docstring).
"""

from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
import pulp

from src import config


@dataclass
class SquadResult:
    squad: pd.DataFrame          # all 15 players, with a "role" column: XI / BENCH
    starting_xi: pd.DataFrame
    bench_ordered: pd.DataFrame  # bench players sorted by autosub priority (best first)
    captain_id: int
    vice_captain_id: int
    total_cost: float
    expected_points: float       # includes captain doubling
    status: str


def _pick_solver(time_limit: int):
    """Kept for backwards compatibility with anything importing it directly,
    but solve_single_gw() below no longer uses this — see _solve()."""
    return pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit)


def _solve(prob: pulp.LpProblem, time_limit: int) -> str:
    """Tries HiGHS first, falls back to CBC (bundled with PuLP, always
    available) if HiGHS isn't actually runnable on this machine.

    Having `highspy` importable in Python doesn't guarantee `pulp.HiGHS_CMD`
    can run — HiGHS_CMD shells out to a separate `highs`/`highs.exe`
    command-line binary, which is a different thing from the `highspy`
    Python package. So the only reliable check is to actually try solving
    and catch the failure, rather than checking if `import highspy` works.

    Returns the name of whichever solver actually ran, for a friendly message.
    """
    try:
        print("Solving with HiGHS...")
        prob.solve(pulp.HiGHS_CMD(msg=False, timeLimit=time_limit))
        print("Solved with HiGHS.")
        return "HiGHS"
    except Exception as e:
        print(f"HiGHS solver unavailable ({e}); falling back to CBC (bundled with PuLP) ...")
        prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
        print("Solved with CBC.")
        return "CBC"


def solve_single_gw(
    players: pd.DataFrame,
    xpts_col: str,
    budget: float = config.BUDGET,
    locked_in_ids: list[int] | None = None,   # players you force INTO the squad
    banned_ids: list[int] | None = None,      # players to force OUT (injuries etc.)
    time_limit: int = config.SOLVER_TIME_LIMIT_SECONDS,
) -> SquadResult:
    locked_in_ids = set(locked_in_ids or [])
    banned_ids = set(banned_ids or [])

    if xpts_col not in players.columns:
        raise SystemExit(f"Column '{xpts_col}' not found. Available projection columns: "
                          f"{players.attrs.get('gw_columns', [])}")

    df = players[~players["player_id"].isin(banned_ids)].reset_index(drop=True)
    if len(df) < config.SQUAD_SIZE:
        raise SystemExit(f"Only {len(df)} eligible players after exclusions — need at least "
                          f"{config.SQUAD_SIZE}. Check your 'status' column or banned_ids.")

    ids = df["player_id"].tolist()
    price = dict(zip(df["player_id"], df["price"]))
    pts = dict(zip(df["player_id"], df[xpts_col]))
    pos = dict(zip(df["player_id"], df["position"]))
    club = dict(zip(df["player_id"], df["club"]))

    prob = pulp.LpProblem("ekstraklasa_single_gw", pulp.LpMaximize)

    squad = pulp.LpVariable.dicts("squad", ids, cat="Binary")
    lineup = pulp.LpVariable.dicts("lineup", ids, cat="Binary")
    captain = pulp.LpVariable.dicts("captain", ids, cat="Binary")

    # Objective: starters score once, captain scores an extra time (i.e. double total)
    prob += pulp.lpSum(
        pts[i] * lineup[i] + pts[i] * captain[i] * (config.CAPTAIN_MULTIPLIER - 1)
        for i in ids
    )

    # Squad composition
    prob += pulp.lpSum(squad[i] for i in ids) == config.SQUAD_SIZE
    for p, count in config.SQUAD_POSITION_LIMITS.items():
        prob += pulp.lpSum(squad[i] for i in ids if pos[i] == p) == count

    # Budget
    prob += pulp.lpSum(price[i] * squad[i] for i in ids) <= budget

    # Club limit
    for c in set(club.values()):
        prob += pulp.lpSum(squad[i] for i in ids if club[i] == c) <= config.MAX_PLAYERS_PER_CLUB

    # Lineup must be a subset of squad, exactly 11 starters
    for i in ids:
        prob += lineup[i] <= squad[i]
    prob += pulp.lpSum(lineup[i] for i in ids) == config.STARTING_XI

    # Formation validity
    for p, (lo, hi) in config.LINEUP_POSITION_RANGES.items():
        prob += pulp.lpSum(lineup[i] for i in ids if pos[i] == p) >= lo
        prob += pulp.lpSum(lineup[i] for i in ids if pos[i] == p) <= hi

    # Exactly one captain, must be a starter
    for i in ids:
        prob += captain[i] <= lineup[i]
    prob += pulp.lpSum(captain[i] for i in ids) == 1

    # Forced picks / bans
    missing_locks = locked_in_ids - set(ids)
    if missing_locks:
        print(f"Warning: locked_in_ids {missing_locks} aren't in the eligible player pool — ignored.")
    for i in locked_in_ids:
        if i in squad:
            prob += squad[i] == 1

    _solve(prob, time_limit)

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise SystemExit(
            f"Solver finished with status '{status}' instead of Optimal — this usually means "
            f"the constraints can't all be satisfied (e.g. budget too tight, not enough players "
            f"of some position, or conflicting locked_in_ids/banned_ids). Try relaxing budget or "
            f"reviewing your player pool."
        )

    squad_ids = [i for i in ids if squad[i].value() == 1]
    xi_ids = [i for i in ids if lineup[i].value() == 1]
    bench_ids = [i for i in squad_ids if i not in xi_ids]
    cap_id = next(i for i in ids if captain[i].value() == 1)

    squad_df = df[df["player_id"].isin(squad_ids)].copy()
    xi_df = df[df["player_id"].isin(xi_ids)].copy()
    bench_df = df[df["player_id"].isin(bench_ids)].copy()

    # Bench order: this doesn't affect the score directly, so it's decided
    # post-solve, not inside the ILP. Priority = highest xPts first, except
    # the reserve GK sits first, then outfield reserves ranked by xPts (your
    # league's autosub logic differs).
    #
    # SIMPLIFICATION: the official autosub logic also has to respect formation
    # validity when it substitutes (e.g. an outfield reserve won't come on if
    # doing so would create an illegal formation — see the "1-2-5-3" example
    # on the rules page). This ordering doesn't check that; it's a reasonable
    # approximation, not an exact reproduction of the site's autosub engine.
    bench_gk = bench_df[bench_df["position"] == "GK"]
    bench_outfield = bench_df[bench_df["position"] != "GK"].sort_values(xpts_col, ascending=False)
    bench_ordered = pd.concat([bench_gk, bench_outfield])

    # Vice-captain: highest xPts starter who isn't the captain (simple, safe default)
    vice_id = xi_df[xi_df["player_id"] != cap_id].sort_values(xpts_col, ascending=False).iloc[0]["player_id"]

    xi_points = xi_df[xpts_col].sum()
    captain_bonus = pts[cap_id] * (config.CAPTAIN_MULTIPLIER - 1)

    return SquadResult(
        squad=squad_df,
        starting_xi=xi_df,
        bench_ordered=bench_ordered,
        captain_id=cap_id,
        vice_captain_id=int(vice_id),
        total_cost=squad_df["price"].sum(),
        expected_points=xi_points + captain_bonus,
        status=status,
    )

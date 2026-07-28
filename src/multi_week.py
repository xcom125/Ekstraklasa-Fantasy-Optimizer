"""
Multi-gameweek horizon solver.

Solves ALL weeks in the horizon simultaneously (a single joint ILP), so the
squad chosen for week 1 already accounts for who you'll want in week 3 —
this is what makes it worth more than solving each week greedily.

Transfer model:
  - week 1 is a free build (no transfer cost) if no initial_squad_ids is
    given — matches the official rule that transfer fees don't apply before
    the season starts or when creating a brand new team mid-season.
  - each week after that, transfers_used[w] = number of players swapped
    vs week w-1
  - free_transfers per week is fixed by config.FREE_TRANSFERS_PER_WEEK (2,
    per the official rules — banking unused transfers is not part of the
    official rules and is left off by default, see config.BANK_TRANSFERS)
  - hits[w] = max(0, transfers_used[w] - free_transfers_available[w]),
    costing config.POINTS_HIT_COST (-3) points each
  - a Wildcard week (pass its week number in wildcard_weeks) makes transfers
    free that week — official rule: usable once per SEASON, Premium only;
    this solver doesn't enforce the "once per season" / Premium restriction,
    that's on you to respect when picking wildcard_weeks

Simplification (documented, not hidden): sell price == buy price, i.e. no
price-rise/fall modelling. That's the single biggest thing a paid tool adds
over this; add it later by tracking a separate `sell_price` per player per
week if you start scraping price-change data.

Chips other than Wildcard (Ekstra Transfer, Kapitanów Dwóch, Ławka Punktuje,
Joker — see config.CHIPS) are documented there but NOT enforced by this ILP
yet. Modelling them fully means: Ekstra Transfer → treat like a 1-week
+1 to free_transfers (small change, similar to wildcard_weeks handling);
Kapitanów Dwóch → double the vice-captain's contribution to the objective
for that week; Ławka Punktuje → add all 4 bench players' points to that
week's objective term unconditionally; Joker → a new binary "which starter
is doubled" variable constrained to price <= 2.0M and excluding the captain.
None of these are difficult, they just weren't the priority for this pass —
flagging clearly rather than quietly pretending they're handled.
"""

from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
import pulp

from src import config


@dataclass
class WeekResult:
    week: int
    starting_xi: pd.DataFrame
    bench_ordered: pd.DataFrame
    captain_id: int
    vice_captain_id: int
    transfers_in: list
    transfers_out: list
    hit_taken: int
    free_transfers_available: int   # FT you had going into this week (before this week's transfers)
    squad_cost: float                # total squad value this week — used to derive ITB
    points_this_week: float   # xPts, after captain + hit cost


@dataclass
class HorizonResult:
    weeks: list  # list[WeekResult]
    total_expected_points: float
    status: str


def _pick_solver(time_limit: int):
    """Kept for backwards compatibility; solve_horizon() below uses _solve() instead."""
    return pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit)


def _solve(prob: pulp.LpProblem, time_limit: int) -> str:
    """Tries HiGHS first, falls back to CBC (bundled with PuLP) if HiGHS
    isn't actually runnable — see the matching docstring in src/solver.py
    for why an `import highspy` check alone isn't a reliable test."""
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


def solve_horizon(
    players: pd.DataFrame,
    gw_columns: list[str],           # e.g. ["xpts_gw1", ..., "xpts_gw5"], in order
    budget: float = config.BUDGET,
    initial_squad_ids: list[int] | None = None,
    wildcard_weeks: set[int] | None = None,   # 1-indexed week numbers within horizon
    decay: float = config.DEFAULT_DECAY,
    time_limit: int = config.SOLVER_TIME_LIMIT_SECONDS,
) -> HorizonResult:
    wildcard_weeks = wildcard_weeks or set()
    weeks = list(range(1, len(gw_columns) + 1))

    df = players.reset_index(drop=True)
    if len(df) < config.SQUAD_SIZE:
        raise SystemExit(f"Only {len(df)} eligible players — need at least {config.SQUAD_SIZE}.")

    if initial_squad_ids is not None:
        missing = set(initial_squad_ids) - set(df["player_id"])
        if missing:
            raise SystemExit(f"initial_squad_ids contains player_id(s) not in the player pool: {missing}")
        if len(initial_squad_ids) != config.SQUAD_SIZE:
            raise SystemExit(f"initial_squad_ids has {len(initial_squad_ids)} players, "
                              f"expected exactly {config.SQUAD_SIZE}.")

    ids = df["player_id"].tolist()
    price = dict(zip(df["player_id"], df["price"]))
    pos = dict(zip(df["player_id"], df["position"]))
    club = dict(zip(df["player_id"], df["club"]))
    pts = {  # pts[(player, week)]
        (i, w): float(df.loc[df["player_id"] == i, gw_columns[w - 1]].iloc[0])
        for i in ids for w in weeks
    }

    print(f"Building joint ILP for {len(weeks)} gameweek(s), {len(ids)} candidate players "
          f"({'fresh build' if initial_squad_ids is None else 'starting from existing squad'})"
          + (f", wildcard on week(s) {sorted(wildcard_weeks)}" if wildcard_weeks else "") + " ...")

    prob = pulp.LpProblem("ekstraklasa_horizon", pulp.LpMaximize)

    squad = pulp.LpVariable.dicts("squad", (ids, weeks), cat="Binary")
    lineup = pulp.LpVariable.dicts("lineup", (ids, weeks), cat="Binary")
    captain = pulp.LpVariable.dicts("captain", (ids, weeks), cat="Binary")
    transfer_in = pulp.LpVariable.dicts("transfer_in", (ids, weeks), cat="Binary")
    transfer_out = pulp.LpVariable.dicts("transfer_out", (ids, weeks), cat="Binary")
    hits = pulp.LpVariable.dicts("hits", weeks, lowBound=0, cat="Integer")
    if config.BANK_TRANSFERS:
        ft_bank = pulp.LpVariable.dicts("ft_bank", weeks, lowBound=0, upBound=config.MAX_BANKED_FT, cat="Integer")

    # ---- per-week squad validity ----
    for w in weeks:
        prob += pulp.lpSum(squad[i][w] for i in ids) == config.SQUAD_SIZE
        for p, count in config.SQUAD_POSITION_LIMITS.items():
            prob += pulp.lpSum(squad[i][w] for i in ids if pos[i] == p) == count
        prob += pulp.lpSum(price[i] * squad[i][w] for i in ids) <= budget
        for c in set(club.values()):
            prob += pulp.lpSum(squad[i][w] for i in ids if club[i] == c) <= config.MAX_PLAYERS_PER_CLUB

        for i in ids:
            prob += lineup[i][w] <= squad[i][w]
        prob += pulp.lpSum(lineup[i][w] for i in ids) == config.STARTING_XI
        for p, (lo, hi) in config.LINEUP_POSITION_RANGES.items():
            prob += pulp.lpSum(lineup[i][w] for i in ids if pos[i] == p) >= lo
            prob += pulp.lpSum(lineup[i][w] for i in ids if pos[i] == p) <= hi

        for i in ids:
            prob += captain[i][w] <= lineup[i][w]
        prob += pulp.lpSum(captain[i][w] for i in ids) == 1

    # ---- transfer linking between consecutive weeks ----
    # BUG FIX: the previous version only lower-bounded transfer_in/transfer_out
    # (">="), with no cost anywhere in the objective forcing transfer_out back
    # down to 0 when a player DIDN'T actually leave the squad. Since nothing
    # penalized transfer_out=1 for a player who stayed, the solver was free to
    # set it to 1 for almost the entire player pool — which is exactly the
    # "OUT: #1, #2, #3, ..." bug. Fixed by pinning both variables exactly to
    # the squad change with an equality, plus a mutual-exclusion constraint
    # (a player can't be transferred in AND out in the same week):
    #     transfer_in[i][w] - transfer_out[i][w] == squad[i][w] - squad[i][w-1]
    #     transfer_in[i][w] + transfer_out[i][w] <= 1
    # This has exactly one solution for each of the three possible squad
    # transitions (stayed / entered / left), so the values are no longer
    # left to the solver's discretion.
    for i in ids:
        prev_in_squad = int(i in initial_squad_ids) if initial_squad_ids is not None else None

        for idx, w in enumerate(weeks):
            if idx == 0:
                if prev_in_squad is None:
                    continue  # no known starting squad: week 1 is a free build, no transfer cost
                prob += transfer_in[i][w] - transfer_out[i][w] == squad[i][w] - prev_in_squad
            else:
                w_prev = weeks[idx - 1]
                prob += transfer_in[i][w] - transfer_out[i][w] == squad[i][w] - squad[i][w_prev]
            prob += transfer_in[i][w] + transfer_out[i][w] <= 1

    # ---- free transfers / hits per week ----
    ft_display = {}   # week -> FT value to show in the console/CSV output
    for idx, w in enumerate(weeks):
        if idx == 0 and initial_squad_ids is None:
            ft_display[w] = config.FREE_TRANSFERS_PER_WEEK
            continue  # first-ever build: no transfer accounting needed

        transfers_used = pulp.lpSum(transfer_in[i][w] for i in ids)

        if w in wildcard_weeks:
            prob += hits[w] == 0  # wildcard: unlimited free transfers
            ft_display[w] = "WC"
            continue

        if config.BANK_TRANSFERS:
            # NOTE: banking is left as a simplified/experimental feature.
            # Correctly modelling ft_bank[w] = min(ft_bank[w-1] - used[w-1] + 1, cap)
            # needs a couple of extra linearisation constraints (min() isn't
            # linear). Until those are added, this just caps at MAX_BANKED_FT
            # without properly carrying the previous week's leftover — good
            # enough to experiment with, not to trust for real hit decisions.
            # Keep config.BANK_TRANSFERS = False (default) for correct results.
            if idx == 0:
                available_ft = config.FREE_TRANSFERS_PER_WEEK
            else:
                prob += ft_bank[w] <= config.MAX_BANKED_FT
                available_ft = ft_bank[w]
        else:
            available_ft = config.FREE_TRANSFERS_PER_WEEK
        ft_display[w] = config.FREE_TRANSFERS_PER_WEEK

        prob += hits[w] >= transfers_used - available_ft
        prob += hits[w] >= 0

    # ---- objective: sum of (starting XI points + captain bonus) * decay, minus hit costs ----
    objective_terms = []
    for idx, w in enumerate(weeks):
        weight = decay ** idx
        week_points = pulp.lpSum(
            pts[(i, w)] * lineup[i][w] + pts[(i, w)] * captain[i][w] * (config.CAPTAIN_MULTIPLIER - 1)
            for i in ids
        )
        objective_terms.append(weight * week_points)
        if not (idx == 0 and initial_squad_ids is None):
            objective_terms.append(-weight * config.POINTS_HIT_COST * hits[w])

    prob += pulp.lpSum(objective_terms)

    _solve(prob, time_limit)
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise SystemExit(
            f"Solver finished with status '{status}' instead of Optimal across the {len(weeks)}-week "
            f"horizon — check that your budget, player pool, and initial_squad_ids (if any) are "
            f"mutually feasible for every week. A longer time_limit can also help on larger horizons."
        )

    results = []
    total_pts = 0.0
    for idx, w in enumerate(weeks):
        squad_ids = [i for i in ids if squad[i][w].value() == 1]
        xi_ids = [i for i in ids if lineup[i][w].value() == 1]
        bench_ids = [i for i in squad_ids if i not in xi_ids]
        cap_id = next(i for i in ids if captain[i][w].value() == 1)

        xi_df = df[df["player_id"].isin(xi_ids)].copy()
        bench_df = df[df["player_id"].isin(bench_ids)].copy()
        bench_gk = bench_df[bench_df["position"] == "GK"]
        bench_outfield = bench_df[bench_df["position"] != "GK"].sort_values(gw_columns[idx], ascending=False)
        bench_ordered = pd.concat([bench_gk, bench_outfield])

        # vice-captain: highest-xPts starter who isn't the captain (same
        # simple, safe default used in the single-gameweek solver)
        vice_id = xi_df[xi_df["player_id"] != cap_id].sort_values(gw_columns[idx], ascending=False).iloc[0]["player_id"]

        ins = [i for i in ids if transfer_in[i][w].value() == 1] if (idx > 0 or initial_squad_ids is not None) else []
        outs = [i for i in ids if transfer_out[i][w].value() == 1] if (idx > 0 or initial_squad_ids is not None) else []
        hit = int(hits[w].value()) if (idx > 0 or initial_squad_ids is not None) and w not in wildcard_weeks else 0

        week_pts = xi_df[gw_columns[idx]].sum() + pts[(cap_id, w)] * (config.CAPTAIN_MULTIPLIER - 1) - hit * config.POINTS_HIT_COST
        total_pts += week_pts

        results.append(WeekResult(
            week=w,
            starting_xi=xi_df,
            bench_ordered=bench_ordered,
            captain_id=cap_id,
            vice_captain_id=int(vice_id),
            transfers_in=ins,
            transfers_out=outs,
            hit_taken=hit,
            free_transfers_available=ft_display.get(w, config.FREE_TRANSFERS_PER_WEEK),
            squad_cost=sum(price[i] for i in squad_ids),
            points_this_week=week_pts,
        ))

    return HorizonResult(weeks=results, total_expected_points=total_pts, status=status)

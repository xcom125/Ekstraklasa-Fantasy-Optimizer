"""Turn a SquadResult / HorizonResult into console output + CSVs, in the
compact gameweek-by-gameweek style used by open-fpl-solver
(https://github.com/solioanalytics/open-fpl-solver) and similar FPL tools:

    ** GW 1:
    ITB=0.0->1.2, FT=2, PT=0, NT=2
    Buy 23 - Kamiński
    Sell 41 - Nowak
    Lineup:
        Raya (3.99)
        Mukiele (4.09), Lacroix (4.21), Thiaw (4.49), Hall (4.49)
        Saka (5.27), Szoboszlai (5.58), Gakpo (5.68)
        Thiago (4.19), Ekitiké (6.06, V), Haaland (6.18, C)
    Bench:
        Dúbravka (2.68), Stach (3.84), Andersen (3.72), Cherki (3.68)
    Lineup xPts: 60.41

Field meanings:
    ITB = In The Bank, shown as (before this week's transfers) -> (after)
    FT  = Free Transfers available this week ("WC" if Wildcard is active)
    PT  = Points hiT (the -3-per-transfer cost paid for hits this week)
    NT  = Number of Transfers made this week
The number after each lineup player is THAT PLAYER'S xPts for the
gameweek (not their price) — ", C" / ", V" mark the captain/vice-captain.
"""

from __future__ import annotations
import os
import pandas as pd

from src import config

POSITION_ORDER = ["GK", "DEF", "MID", "FWD"]


def _fmt_player(row: pd.Series, gw_col: str, cap_id: int, vice_id: int | None) -> str:
    tag = ""
    if row["player_id"] == cap_id:
        tag = ", C"
    elif vice_id is not None and row["player_id"] == vice_id:
        tag = ", V"
    return f"{row['name']} ({row[gw_col]:.2f}{tag})"


def _fmt_lineup_lines(xi_df: pd.DataFrame, gw_col: str, cap_id: int, vice_id: int | None, indent: str) -> str:
    lines = []
    for pos in POSITION_ORDER:
        grp = xi_df[xi_df["position"] == pos]
        if len(grp):
            lines.append(indent + ", ".join(_fmt_player(r, gw_col, cap_id, vice_id) for _, r in grp.iterrows()))
    return "\n".join(lines)


def _fmt_bench_line(bench_df: pd.DataFrame, gw_col: str, indent: str) -> str:
    return indent + ", ".join(f"{row['name']} ({row[gw_col]:.2f})" for _, row in bench_df.iterrows())


def export_single_gw(result, out_dir: str = "exports", label: str = "gw"):
    os.makedirs(out_dir, exist_ok=True)

    xi = result.starting_xi.copy()
    xi["role"] = "XI"
    xi.loc[xi["player_id"] == result.captain_id, "role"] = "XI (C)"
    xi.loc[xi["player_id"] == result.vice_captain_id, "role"] = "XI (VC)"

    bench = result.bench_ordered.copy()
    bench["role"] = [f"BENCH {i+1}" for i in range(len(bench))]

    full = pd.concat([xi, bench])[["player_id", "name", "club", "position", "price", "role"]]
    path = os.path.join(out_dir, f"{label}_squad.csv")
    full.to_csv(path, index=False)

    gw_col = label if label.startswith("xpts_gw") else None
    print()
    print(f"** {label}:")
    print(f"Cost={result.total_cost:.1f}/{config.BUDGET:.1f}")
    print("Lineup:")
    if gw_col and gw_col in xi.columns:
        print(_fmt_lineup_lines(xi, gw_col, result.captain_id, result.vice_captain_id, "    "))
        print("Bench:")
        print(_fmt_bench_line(bench, gw_col, "    "))
    else:
        # fall back to the plain price-based table if the gw column name
        # isn't derivable from the label
        for _, row in full.iterrows():
            print(f"  {row['role']:<10} {row['position']:<4} {row['name']:<25} "
                  f"{row['club']:<15} {row['price']:.1f}M")
    print(f"Lineup xPts: {result.expected_points:.2f}")
    print(f"Saved to {path}")
    return path


def export_horizon(result, out_dir: str = "exports", label: str = "horizon",
                    next_round: int = 1, initial_itb: float = 0.0):
    """
    next_round: real-world gameweek number that result.weeks[0] corresponds
    to — only affects the displayed "GW N" numbers, not the underlying model.
    initial_itb: cash already in the bank before week 1 (e.g. from your
    settings.json "itb" field) — shown as week 1's "before" ITB value.
    """
    os.makedirs(out_dir, exist_ok=True)

    # Build a player_id -> "Name (Club)" lookup from every squad across the
    # whole horizon, so transfer lists show names instead of raw IDs.
    id_to_label = {}
    for wk in result.weeks:
        for _, row in pd.concat([wk.starting_xi, wk.bench_ordered]).iterrows():
            id_to_label[row["player_id"]] = row["name"]

    rows = []
    prev_cost = None
    path_lines = []
    effective_budget = config.BUDGET + initial_itb   # total spending power available each week
    for wk in result.weeks:
        gw_col = f"xpts_gw{wk.week}"
        display_gw = wk.week + next_round - 1

        xi = wk.starting_xi.copy()
        xi["role"] = "XI"
        xi.loc[xi["player_id"] == wk.captain_id, "role"] = "XI (C)"
        xi.loc[xi["player_id"] == wk.vice_captain_id, "role"] = "XI (VC)"
        bench = wk.bench_ordered.copy()
        bench["role"] = [f"BENCH {i+1}" for i in range(len(bench))]
        wk_df = pd.concat([xi, bench])
        wk_df["week"] = display_gw
        wk_df["transfers_in"] = ", ".join(f"{i} - {id_to_label.get(i, '?')}" for i in wk.transfers_in)
        wk_df["transfers_out"] = ", ".join(f"{i} - {id_to_label.get(i, '?')}" for i in wk.transfers_out)
        wk_df["hit_taken"] = wk.hit_taken
        rows.append(wk_df)

        # --- console block for this week, in the requested format ---
        itb_after = effective_budget - wk.squad_cost
        if prev_cost is not None:
            itb_before = effective_budget - prev_cost   # leftover carried from last week
        else:
            itb_before = initial_itb   # cash already banked before week 1 (fresh build -> 0.0 default)
        prev_cost = wk.squad_cost
        pt = wk.hit_taken * config.POINTS_HIT_COST
        nt = len(wk.transfers_in)

        print()
        print(f"** GW {display_gw}:")
        print(f"ITB={itb_before:.1f}->{itb_after:.1f}, FT={wk.free_transfers_available}, PT={pt}, NT={nt}")
        for pid in wk.transfers_in:
            print(f"Buy {pid} - {id_to_label.get(pid, '?')}")
        for pid in wk.transfers_out:
            print(f"Sell {pid} - {id_to_label.get(pid, '?')}")
        print("Lineup:")
        print(_fmt_lineup_lines(xi, gw_col, wk.captain_id, wk.vice_captain_id, "    "))
        print("Bench:")
        print(_fmt_bench_line(bench, gw_col, "    "))
        print(f"Lineup xPts: {wk.points_this_week:.2f}")

        # --- one-line path summary for this week ---
        if wk.free_transfers_available == "WC":
            path_lines.append(f"GW{display_gw}: UNLIMITED TRANSFER")
        elif wk.transfers_in or wk.transfers_out:
            outs = ", ".join(id_to_label.get(i, "?") for i in wk.transfers_out)
            ins = ", ".join(id_to_label.get(i, "?") for i in wk.transfers_in)
            path_lines.append(f"GW{display_gw}: {outs} -> {ins}")
        else:
            path_lines.append(f"GW{display_gw}: Roll")

    full = pd.concat(rows)[
        ["week", "player_id", "name", "club", "position", "price", "role",
         "transfers_in", "transfers_out", "hit_taken"]
    ]
    path = os.path.join(out_dir, f"{label}.csv")
    full.to_csv(path, index=False)

    print()
    print(f"Total expected points across horizon: {result.total_expected_points:.2f}")
    print(f"Saved to {path}")

    print()
    print("PATH 1:")
    for line in path_lines:
        print(f"  {line}")

    return path

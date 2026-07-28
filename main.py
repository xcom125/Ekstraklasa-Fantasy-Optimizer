"""
Two ways to run this:

1. Settings-driven (recommended — this is the "just solve" workflow):
       python main.py run --settings settings.json
   Edit settings.json (copy settings.example.json to start) for your next
   gameweek's team, transfers-so-far, chip plans, etc. — see that file and
   src/settings.py for every field.

2. Quick CLI-only, no settings file:
       python main.py single --data data/players_example.csv --gw xpts_gw1
       python main.py horizon --data data/players_example.csv --weeks 5

Run `python main.py --help`, or `python main.py <mode> --help`, for all options.
"""
import argparse
import sys
from src import data_loader, solver, multi_week, export, settings as settings_mod


def run_single(args):
    df = data_loader.available_players(data_loader.load_players(args.data), xmin_lb=args.xmin_lb)
    if args.gw not in df.columns:
        available = df.attrs.get("gw_columns", [])
        raise SystemExit(f"--gw '{args.gw}' not found. Available projection columns: {available}")
    print(f"Solving single gameweek ({args.gw}) with budget {args.budget}M ...")
    result = solver.solve_single_gw(df, xpts_col=args.gw, budget=args.budget)
    export.export_single_gw(result, label=args.gw)


def run_horizon(args):
    df = data_loader.available_players(data_loader.load_players(args.data), xmin_lb=args.xmin_lb)
    gw_cols = df.attrs["gw_columns"][: args.weeks]
    if len(gw_cols) < args.weeks:
        raise SystemExit(
            f"Only {len(gw_cols)} xpts_gw* column(s) found in the CSV but --weeks={args.weeks} "
            f"was requested. Add more xpts_gw<N> columns or lower --weeks."
        )
    wildcard_weeks = set(args.wildcard) if args.wildcard else None
    if wildcard_weeks and max(wildcard_weeks) > len(gw_cols):
        raise SystemExit(f"--wildcard week {max(wildcard_weeks)} is outside the {len(gw_cols)}-week horizon.")
    result = multi_week.solve_horizon(
        df, gw_columns=gw_cols, budget=args.budget, wildcard_weeks=wildcard_weeks
    )
    export.export_horizon(result, label=f"horizon_{args.weeks}gw")


def run_settings_driven(args):
    s = settings_mod.Settings.load(args.settings)
    print(f"Loaded settings from '{args.settings}': horizon={s.horizon}, next_round={s.next_round}, "
          f"ft={s.ft}, itb={s.itb}, decay_base={s.decay_base}, xmin_lb={s.xmin_lb}, "
          f"{'fresh build' if not s.initial_squad else f'{len(s.initial_squad)}-player initial squad'}.")

    df = data_loader.available_players(data_loader.load_players(s.projection_file), xmin_lb=s.xmin_lb)
    gw_cols = df.attrs["gw_columns"][: s.horizon]
    if len(gw_cols) < s.horizon:
        raise SystemExit(
            f"Only {len(gw_cols)} xpts_gw* column(s) found in '{s.projection_file}' but "
            f"settings.horizon={s.horizon} was requested. Add more xpts_gw<N> columns or lower horizon."
        )

    # config.FREE_TRANSFERS_PER_WEEK is a global constant in src/config.py; if
    # your settings.json's "ft" differs (e.g. you're mid-season with a
    # different rolling count), override it for this run only.
    from src import config
    if s.ft != config.FREE_TRANSFERS_PER_WEEK:
        config.FREE_TRANSFERS_PER_WEEK = s.ft

    effective_budget = config.BUDGET + s.itb
    result = multi_week.solve_horizon(
        df, gw_columns=gw_cols, budget=effective_budget,
        initial_squad_ids=s.initial_squad or None,
        wildcard_weeks=s.wildcard_weeks_relative() or None,
        decay=s.decay_base,
    )
    export.export_horizon(result, label=f"horizon_{s.horizon}gw", next_round=s.next_round, initial_itb=s.itb)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ekstraklasa Fantasy free optimizer (PuLP + CBC/HiGHS, no paid APIs)."
    )
    parser.add_argument("--data", default="data/players_example.csv",
                         help="Path or published Google Sheet CSV URL (default: data/players_example.csv)")
    parser.add_argument("--budget", type=float, default=30.0, help="Budget in millions (default: 30.0)")

    sub = parser.add_subparsers(dest="mode", required=True)

    p_run = sub.add_parser("run", help="Settings-driven run (recommended) — see settings.example.json")
    p_run.add_argument("--settings", default="settings.json",
                        help="Path to your settings JSON file (default: settings.json)")
    p_run.set_defaults(func=run_settings_driven)

    p_single = sub.add_parser("single", help="Solve one gameweek (quick CLI-only mode)")
    p_single.add_argument("--gw", default="xpts_gw1", help="Column name to optimize against (default: xpts_gw1)")
    p_single.add_argument("--xmin-lb", type=float, default=0.0, dest="xmin_lb",
                           help="Exclude players with predicted minutes below this (needs an 'xmins' column)")
    p_single.set_defaults(func=run_single)

    p_horizon = sub.add_parser("horizon", help="Solve a multi-week horizon jointly (quick CLI-only mode)")
    p_horizon.add_argument("--weeks", type=int, default=5, help="Number of gameweeks in the horizon (default: 5)")
    p_horizon.add_argument("--wildcard", type=int, nargs="*", default=None,
                            help="Week number(s) (1-indexed within the horizon) to play Wildcard on, e.g. --wildcard 3")
    p_horizon.add_argument("--xmin-lb", type=float, default=0.0, dest="xmin_lb",
                            help="Exclude players with predicted minutes below this (needs an 'xmins' column)")
    p_horizon.set_defaults(func=run_horizon)

    args = parser.parse_args()
    try:
        args.func(args)
    except SystemExit as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

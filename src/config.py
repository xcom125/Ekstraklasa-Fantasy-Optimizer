"""
Central place for every rule of LOTTO Fantasy Ekstraklasa 2026/27.
Change numbers here if the league changes rules — nothing else should
need touching.

Scoring constants below are transcribed directly from the official rules
page: https://fantasy.ekstraklasa.org/page/howto (section "PUNKTACJA",
fetched July 2026). If the site updates its scoring, update this file only —
projections.py imports everything from here.
"""

# ---- squad & budget ----
BUDGET = 30.0                 # 30M budget, expressed in millions (e.g. a player priced
                               # at "8.5" in-game = 8.5 in this unit). Keep player CSV
                               # prices in the same unit so BUDGET and price columns match.
SQUAD_SIZE = 15
STARTING_XI = 11
BENCH_SIZE = SQUAD_SIZE - STARTING_XI  # 4 (1 GK + 3 outfield, per official rules)

SQUAD_POSITION_LIMITS = {      # exact counts required in the 15-man squad
    "GK": 2,
    "DEF": 5,
    "MID": 5,
    "FWD": 3,
}

# Valid starting-XI position ranges (min, max). The official rules list only
# 7 explicit formations: 3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-3-2, 5-4-1.
# Constraining DEF to [3,5], MID to [3,5], FWD to [1,3] (with GK fixed at 1,
# 11 total) reproduces *exactly* those 7 combinations and no others — verified
# by enumeration, so no extra formation-list constraint is needed in the ILP.
LINEUP_POSITION_RANGES = {
    "GK":  (1, 1),
    "DEF": (3, 5),
    "MID": (3, 5),   # NOTE: 3, not 2 — official formations never field 2 midfielders
    "FWD": (1, 3),
}

MAX_PLAYERS_PER_CLUB = 3

# ---- transfers ----
FREE_TRANSFERS_PER_WEEK = 2      # first 2 transfers/week are free (official rule)
BANK_TRANSFERS = False            # official rules do NOT mention banking unused free
                                   # transfers — leave False unless the league adds it
MAX_BANKED_FT = 5
POINTS_HIT_COST = 3                # -3 pts per transfer beyond the free ones

CAPTAIN_MULTIPLIER = 2             # captain scores double (standard; not explicitly
                                    # re-stated on the howto page but implied by the
                                    # "KAPITANÓW DWÓCH" chip description, which doubles
                                    # BOTH captain and vice-captain instead of just one)

# ---- chip / special-activity behaviour ----
# Names below match the official Polish terms so they're easy to cross-reference
# against the rules page. Only "wildcard" is fully wired into multi_week.py today;
# the rest are modelled here for completeness but not yet enforced by the ILP —
# see the docstring in multi_week.py for what "not yet wired" means in practice.
CHIPS = {
    # Dzika Karta — once per SEASON (not per week), Premium only.
    # Unlimited free transfers that gameweek, no hit.
    "wildcard": {"unlimited_transfers": True, "no_hit": True, "once_per": "season"},

    # Ekstra Transfer — once per season, Premium only.
    # Makes the 3rd transfer that week free too (i.e. +1 free transfer).
    "ekstra_transfer": {"extra_free_transfers": 1, "once_per": "season"},

    # Kapitanów Dwóch — once per season. Vice-captain also scores double
    # (normally only the captain does).
    "double_captains": {"vice_captain_doubles": True, "once_per": "season"},

    # Ławka Punktuje — once per season. All 4 bench players' points count,
    # not just autosubs.
    "bench_boost": {"all_bench_counts": True, "once_per": "season"},

    # Joker — once per season, Premium only. Doubles the points of whichever
    # STARTING (non-captain) player scored the most that week, but only if
    # that player's price is <= 2.0M.
    "joker": {"doubles_top_noncaptain_starter": True, "max_price": 2.0, "once_per": "season"},
}
# Per official rules: wildcard/ekstra_transfer CANNOT be combined in the same
# week with joker/double_captains/bench_boost.

SOLVER_TIME_LIMIT_SECONDS = 60
DEFAULT_DECAY = 0.9   # per-week discount applied to future gameweeks in multi-week horizon


# ---- official scoring table (PUNKTACJA a-p on the howto page) ----
# Kept here as the single source of truth; projections.py imports these.
GOAL_PTS = {"FWD": 4, "MID": 5, "DEF": 6, "GK": 8}
ASSIST_PTS = {"FWD": 3, "MID": 3, "DEF": 4, "GK": 6}
LOTTO_ASSIST_PTS = 2                    # "asysta LOTTO" — a secondary assist type, all positions
CLEAN_SHEET_PTS = {"DEF": 3, "GK": 3, "MID": 1, "FWD": 0}   # requires 60+ minutes played
PENALTY_SAVE_PTS = 4                    # goalkeepers only
APPEARANCE_STARTING_PTS = 2             # named in starting XI (not minutes-based)
APPEARANCE_BENCH_PTS = 1                # came on / counted from the bench
MATCH_WIN_PTS = 1                       # player's team wins the match
PENALTY_WON_PTS = 2                     # won a penalty for their team
TEAM_OF_THE_WEEK_PTS = 1                # Ekstraklasa's official "team of the round"
SAVES_PER_POINT = 3                     # every 3 shots saved (GK) = 1 pt
GOALS_CONCEDED_PENALTY = 1              # GK/DEF only, per goal conceded EXCLUDING the first
YELLOW_CARD_PENALTY = 1
RED_CARD_PENALTY = 3
MISSED_PENALTY_PENALTY = 3
OWN_GOAL_PENALTY = 3
PENALTY_CAUSED_PENALTY = 2              # conceded/caused a penalty against their own team

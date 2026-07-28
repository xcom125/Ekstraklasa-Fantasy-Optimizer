"""
A transparent xPts model matching the official LOTTO Fantasy Ekstraklasa
scoring rules (https://fantasy.ekstraklasa.org/page/howto), for when you
don't have (or trust) manual projections yet.

This is a baseline, not a state-of-the-art model — it turns simple per-90
rate estimates (which you can pull from last season's stats or a scout's
gut feel) into an expected-points number, consistently, for every player.
Swap it out later for something fancier (Poisson goals model, xG-based,
etc.); nothing else in the project cares how xPts was produced.

Every term below corresponds to a lettered rule on the official page:
    a) goals            b) assists            c) "LOTTO" assists
    d) clean sheet       e) penalty save        f) appearance
    g) match win         h) won a penalty       i) team of the week
    j) saves (GK)        k) goals conceded       l) yellow card
    m) red card          n) missed penalty       o) own goal
    p) caused a penalty
"""

from __future__ import annotations
import math
import pandas as pd
from src import config


def clean_sheet_prob_from_xga(expected_goals_against: float) -> float:
    """Standard Poisson clean-sheet probability: P(team concedes 0) =
    exp(-lambda), where lambda is the team's expected goals against for the
    match. This is the textbook approximation goals-against models use
    (independent Poisson arrivals) and is exact given that assumption."""
    return math.exp(-max(0.0, expected_goals_against))


def expected_conceded_penalty_pts(expected_goals_against: float) -> float:
    """Exact expected value of rule (k) — 'minus 1 point per goal conceded,
    excluding the first' — for a team whose goals-against this match follows
    Poisson(lambda = expected_goals_against):

        E[max(0, N - 1)] = E[N] - P(N >= 1) = lambda - (1 - exp(-lambda))

    This replaces the old max(0, avg_conceded - 1) linear stand-in, which
    over-penalised low-lambda teams (it ignores that plenty of those matches
    finish 0-0, where the 'excluding the first' discount saves the whole
    penalty) and under-penalised high-lambda ones. The closed-form Poisson
    expectation is exact under the Poisson assumption, not an approximation
    of one, so it costs nothing extra to compute and is strictly more
    accurate at both ends of the distribution.
    """
    lam = max(0.0, expected_goals_against)
    return max(0.0, lam - (1.0 - math.exp(-lam)))


def estimate_xpts(
    position: str,
    start_prob: float = 0.55,        # probability of being named in the starting XI
    bench_prob: float = 0.20,        # probability of being an unused-but-counted sub
    goals_per90: float = 0.0,
    assists_per90: float = 0.0,
    lotto_assists_per90: float = 0.0,
    expected_goals_against: float | None = None,  # team's xGA for the match (Poisson lambda);
                                                   # preferred over the two args below when given
    clean_sheet_prob: float = 0.30,  # only pays out for DEF/GK/MID, needs 60+ min. Ignored
                                      # if expected_goals_against is supplied (derived instead).
    win_prob: float = 0.33,          # probability the player's team wins the match
    penalty_save_prob: float = 0.0,  # GK only
    saves_per_game: float = 0.0,     # GK only, every 3 = 1 pt
    goals_conceded_per_game: float = 1.3,  # GK/DEF only. Ignored if expected_goals_against
                                            # is supplied (derived instead).
    penalty_won_prob: float = 0.0,
    team_of_week_prob: float = 0.0,
    yellow_card_prob: float = 0.12,
    red_card_prob: float = 0.01,
    missed_penalty_prob: float = 0.0,
    own_goal_prob: float = 0.005,
    penalty_caused_prob: float = 0.01,
) -> float:
    """Returns expected fantasy points for one player for one gameweek.

    Passing expected_goals_against (the team's expected goals conceded this
    match, i.e. a Poisson lambda) links rules (d) and (k) to the SAME
    underlying number, the way a real xG-based model should: a team that
    concedes fewer goals on average both keeps more clean sheets AND gives
    up fewer "goal conceded" deductions, instead of tuning clean_sheet_prob
    and goals_conceded_per_game separately as if they were unrelated. If you
    don't have an xGA estimate yet, pass clean_sheet_prob/goals_conceded_per_game
    directly (legacy behaviour) and skip expected_goals_against.
    """
    play_prob = start_prob + bench_prob
    if play_prob <= 0:
        return 0.0

    if expected_goals_against is not None:
        clean_sheet_prob = clean_sheet_prob_from_xga(expected_goals_against)

    # a) goals, b) assists, c) LOTTO assists — scaled by how much of the
    # game they're expected to actually be on the pitch for
    minutes_share = start_prob + 0.3 * bench_prob  # a bench appearance is worth less playing time
    attacking = minutes_share * (
        goals_per90 * config.GOAL_PTS.get(position, 4)
        + assists_per90 * config.ASSIST_PTS.get(position, 3)
        + lotto_assists_per90 * config.LOTTO_ASSIST_PTS
    )

    # d) clean sheet (60+ minutes, so scale by start_prob mainly)
    clean_sheet = start_prob * clean_sheet_prob * config.CLEAN_SHEET_PTS.get(position, 0)

    # e) penalty save, j) saves — goalkeepers only
    gk_bonus = 0.0
    if position == "GK":
        gk_bonus = (
            start_prob * penalty_save_prob * config.PENALTY_SAVE_PTS
            + start_prob * saves_per_game / config.SAVES_PER_POINT
        )

    # f) appearance
    appearance = start_prob * config.APPEARANCE_STARTING_PTS + bench_prob * config.APPEARANCE_BENCH_PTS

    # g) match win
    win_bonus = play_prob * win_prob * config.MATCH_WIN_PTS

    # h) won a penalty, i) team of the week
    misc_bonus = (
        minutes_share * penalty_won_prob * config.PENALTY_WON_PTS
        + start_prob * team_of_week_prob * config.TEAM_OF_THE_WEEK_PTS
    )

    # k) goals conceded — GK/DEF only, first goal conceded is free.
    # Exact Poisson expectation when expected_goals_against is given (see
    # expected_conceded_penalty_pts docstring); otherwise falls back to the
    # older max(0, avg_conceded - 1) linear stand-in for backward compat.
    conceded_penalty = 0.0
    if position in ("GK", "DEF"):
        if expected_goals_against is not None:
            conceded_penalty = start_prob * expected_conceded_penalty_pts(expected_goals_against) \
                * config.GOALS_CONCEDED_PENALTY
        else:
            conceded_penalty = start_prob * max(0.0, goals_conceded_per_game - 1.0) * config.GOALS_CONCEDED_PENALTY

    # l) yellow, m) red, n) missed penalty, o) own goal, p) caused penalty
    discipline = play_prob * (
        yellow_card_prob * config.YELLOW_CARD_PENALTY
        + red_card_prob * config.RED_CARD_PENALTY
        + missed_penalty_prob * config.MISSED_PENALTY_PENALTY
        + own_goal_prob * config.OWN_GOAL_PENALTY
        + penalty_caused_prob * config.PENALTY_CAUSED_PENALTY
    )

    total = (
        attacking + clean_sheet + gk_bonus + appearance + win_bonus
        + misc_bonus - conceded_penalty - discipline
    )
    return round(total, 2)


def build_xpts_column(df: pd.DataFrame, gw_col: str) -> pd.DataFrame:
    """
    Fills df[gw_col] from raw per-90/per-game stat columns if they exist
    (any subset of the estimate_xpts() keyword arguments, named the same way
    in the CSV — e.g. a `goals_per90` column). Leaves df untouched if none of
    those raw columns are present, so this never silently overwrites manual
    projections unless you call it and supply the raw stats.
    """
    raw_cols = {
        "start_prob", "bench_prob", "goals_per90", "assists_per90",
        "lotto_assists_per90", "expected_goals_against", "clean_sheet_prob",
        "win_prob", "penalty_save_prob", "saves_per_game", "goals_conceded_per_game",
        "penalty_won_prob", "team_of_week_prob", "yellow_card_prob",
        "red_card_prob", "missed_penalty_prob", "own_goal_prob",
        "penalty_caused_prob",
    }
    if not raw_cols.intersection(df.columns):
        return df

    def _row_xpts(row):
        kwargs = {c: row[c] for c in raw_cols if c in df.columns and pd.notna(row[c])}
        return estimate_xpts(position=row["position"], **kwargs)

    df[gw_col] = df.apply(_row_xpts, axis=1)
    return df

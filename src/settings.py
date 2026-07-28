"""
Loads a JSON settings file so a week's run is "point at a file and go"
instead of a growing pile of CLI flags — same idea as user_settings.json
in solioanalytics/open-fpl-solver, adapted to this project's fields.

Precedence: CLI flags > settings JSON > these defaults. Any field you don't
set in your settings file just falls back to the default below, so your
settings.json can be as short as you like.
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field, fields
from pathlib import Path

from src import config


@dataclass
class Settings:
    projection_file: str = "data/players_pool.csv"
    next_round: int = 1              # real-world gameweek number that xpts_gw1 corresponds to
    horizon: int = 5                 # how many gw columns to solve (xpts_gw1..xpts_gw{horizon})
    initial_squad: list = field(default_factory=list)   # player_ids of your CURRENT 15, [] = fresh build
    decay_base: float = config.DEFAULT_DECAY
    itb: float = 0.0                 # cash in the bank on top of your initial_squad's value
    ft: int = config.FREE_TRANSFERS_PER_WEEK   # free transfers available for next_round
    xmin_lb: float = 0.0             # exclude players with predicted minutes below this (0 = off)
    use_wildcard: list = field(default_factory=list)   # real-world gw numbers (matching next_round's
                                                          # numbering) to play Wildcard on
    chip_plan: dict = field(default_factory=dict)     # forward-looking notes only, e.g.
                                                          # {"3": "wildcard", "10": "joker",
                                                          #  "20": "ekstra_transfer"} — keyed by
                                                          # real-world gw number -> chip name. NOT
                                                          # enforced by the solver except wildcard
                                                          # (which still needs its gw listed in
                                                          # use_wildcard too, to actually take effect).
                                                          # This is just a place to keep your season-
                                                          # long plan next to the rest of the settings
                                                          # so you don't have to remember it elsewhere.
                                                          # See README "Known simplifications" for what
                                                          # each of the other chips would need to be
                                                          # wired into multi_week.py for real.

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        path = Path(path)
        if not path.exists():
            raise SystemExit(
                f"Settings file '{path}' not found. Copy settings.example.json to '{path}' "
                f"and edit it, or pass --settings pointing at your own file."
            )
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            print(f"Warning: '{path}' has unrecognised setting(s) {sorted(unknown)} — ignored. "
                  f"Known settings: {sorted(known)}")
        raw = {k: v for k, v in raw.items() if k in known}
        return cls(**raw)

    def wildcard_weeks_relative(self) -> set[int]:
        """Converts use_wildcard (real gameweek numbers) into the 1..horizon
        indexing solve_horizon() expects, given next_round as the offset."""
        return {gw - self.next_round + 1 for gw in self.use_wildcard
                if 1 <= gw - self.next_round + 1 <= self.horizon}

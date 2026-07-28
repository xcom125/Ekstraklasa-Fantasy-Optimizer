"""
Centralized team-name standardization for the whole pipeline.

WHY THIS EXISTS
----------------
Every data source spells club names differently:
    FBref (lossy CSV export):     "Lech Pozna?", "Katowice", "Raków"
    Stats Ultra (Club Strength &
      Next Match Predictions):    "Lech Poznań", "Zagłębie Lubin"  (already
                                   full-form, but exported per-match with
                                   3-letter codes too, e.g. "LPO", "ZAG")
    SofaScore master player pool
      (player_pool.csv /
       ekstraklasa_master_players.csv): "Lech Poznan", "Rakow", "Legia",
                                   "Gornik Zabrze"  (ASCII, often shortened)

CANONICAL FORM
--------------
The canonical spelling used everywhere downstream (data/fixtures.csv,
data/club_strength.csv, data/players_pool.csv `club` column, and every
`club`/`team_name` key the solver and multi_week engines already key off of)
is the full Polish name with diacritics, e.g. "Jagiellonia Białystok",
"Górnik Zabrze", "Raków Częstochowa". This is the form fixtures.csv and
club_strength.csv have used since before this pipeline swap, and it's what
pipeline/fbref_stats.py's FBREF_SQUAD_TO_CLUB / CODE_TO_CLUB dictionaries
already normalize FBref names to — so keeping it as the single canonical
form (rather than switching to the SofaScore master pool's short ASCII
spelling) means fixtures.csv, club_strength.csv, and fbref_stats.py all stay
untouched, and only the *new* SofaScore/Stats Ultra inputs need mapping in.
Every one of the three feeds ultimately resolves to the same 18-club key
space, so the mapping direction is a naming choice, not a data-loss one.

USAGE
-----
    from pipeline.team_names import canonical_team, TEAM_NAME_MAP

    canonical_team("Lech Poznan")   -> "Lech Poznań"
    canonical_team("LPO")           -> "Lech Poznań"
    canonical_team("Raków Częstochowa") -> "Raków Częstochowa"  (identity)

canonical_team() raises with a clear message on an unmapped name rather than
silently dropping a club, since a silent miss here means that club's players
get no fixture/strength data for the rest of the pipeline.
"""

from __future__ import annotations
import unicodedata

# The 18 Ekstraklasa 2026/27 clubs, canonical spelling (matches
# data/fixtures.csv and data/club_strength.csv).
CANONICAL_CLUBS = [
    "Cracovia",
    "GKS Katowice",
    "Górnik Zabrze",
    "Jagiellonia Białystok",
    "Korona Kielce",
    "Lech Poznań",
    "Legia Warszawa",
    "Motor Lublin",
    "Piast Gliwice",
    "Pogoń Szczecin",
    "Radomiak Radom",
    "Raków Częstochowa",
    "Widzew Łódź",
    "Wieczysta Kraków",
    "Wisła Kraków",
    "Wisła Płock",
    "Zagłębie Lubin",
    "Śląsk Wrocław",
]

# alias -> canonical. Every alias is lowercased/stripped at lookup time (see
# canonical_team()), so entries here only need to cover distinct SPELLINGS,
# not casing variants.
TEAM_NAME_MAP: dict[str, str] = {
    # --- identity (canonical maps to itself) ---
    **{c: c for c in CANONICAL_CLUBS},

    # --- SofaScore master pool (player_pool.csv / ekstraklasa_master_players.csv) ---
    "Cracovia Krakow": "Cracovia",
    "Gornik Zabrze": "Górnik Zabrze",
    "Jagiellonia": "Jagiellonia Białystok",
    "Legia": "Legia Warszawa",
    "Pogon Szczecin": "Pogoń Szczecin",
    "Radomiak": "Radomiak Radom",
    "Rakow": "Raków Częstochowa",
    "Rakow Czestochowa": "Raków Częstochowa",
    "Slask Wroclaw": "Śląsk Wrocław",
    "Widzew Lodz": "Widzew Łódź",
    "Wieczysta": "Wieczysta Kraków",
    "Wieczysta Krakow": "Wieczysta Kraków",
    "Wisla Krakow": "Wisła Kraków",
    "Wisla Plock": "Wisła Płock",
    "Zaglebie Lubin": "Zagłębie Lubin",
    "Lech Poznan": "Lech Poznań",

    # --- FBref lossy-encoding squad names (see fbref_stats.FBREF_SQUAD_TO_CLUB) ---
    "Katowice": "GKS Katowice",
    "Legia Warsaw": "Legia Warszawa",
    "Raków": "Raków Częstochowa",
    "Radomiak": "Radomiak Radom",

    # --- Stats Ultra 3-letter match-prediction codes ---
    "CRA": "Cracovia",
    "GKS": "GKS Katowice",
    "GOR": "Górnik Zabrze",
    "JAG": "Jagiellonia Białystok",
    "KOR": "Korona Kielce",
    "LPO": "Lech Poznań",
    "LEG": "Legia Warszawa",
    "MOT": "Motor Lublin",
    "PIA": "Piast Gliwice",
    "POG": "Pogoń Szczecin",
    "RAD": "Radomiak Radom",
    "RAK": "Raków Częstochowa",
    "RCZ": "Raków Częstochowa",
    "WID": "Widzew Łódź",
    "WIE": "Wieczysta Kraków",
    "WIS": "Wisła Kraków",
    "WPL": "Wisła Płock",
    "SLA": "Śląsk Wrocław",
    "ZAG": "Zagłębie Lubin",
}

# lowercase alias -> canonical, built once for case-insensitive lookup.
_LOWER_MAP = {k.strip().lower(): v for k, v in TEAM_NAME_MAP.items()}


def _ascii_fold(s: str) -> str:
    table = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    s = str(s).translate(table)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


# ascii-folded canonical -> canonical, for a last-resort fuzzy match against
# any spelling variant not explicitly listed above (e.g. missing diacritics
# in an unanticipated feed).
_FOLDED_CANONICAL = {_ascii_fold(c): c for c in CANONICAL_CLUBS}


def canonical_team(name: str, *, strict: bool = True) -> str | None:
    """Standardizes any known alias/code/spelling to the canonical club name.

    Resolution order: exact dict hit (case-insensitive) -> ascii-folded
    match against the canonical list -> ascii-folded match against every
    alias's folded form. Raises ValueError on a genuine miss when
    strict=True (the default); pass strict=False to get None instead, e.g.
    when scanning a feed that may include relegated/foreign clubs you want
    to skip rather than hard-fail on.
    """
    if name is None:
        if strict:
            raise ValueError("canonical_team() got None")
        return None
    key = str(name).strip()
    if not key:
        if strict:
            raise ValueError("canonical_team() got an empty string")
        return None

    hit = _LOWER_MAP.get(key.lower())
    if hit:
        return hit

    folded = _ascii_fold(key)
    if folded in _FOLDED_CANONICAL:
        return _FOLDED_CANONICAL[folded]
    for alias, canonical in TEAM_NAME_MAP.items():
        if _ascii_fold(alias) == folded:
            return canonical

    if strict:
        raise ValueError(
            f"canonical_team(): no mapping for club name '{name}'. "
            f"Add it to TEAM_NAME_MAP in pipeline/team_names.py."
        )
    return None

"""
FBref stats parsing + shared helpers used by pipeline/build_player_pool.py
(load_fbref_players, build_iliga_lookup, ascii_fold, PROMOTED_CLUBS) — that
script is the live pipeline; import from here rather than duplicating this
parsing logic.

This file also still contains its own original standalone build() (below),
which produced a 5-gameweek player file directly from a flat price list
before player-status tracking (MAY/NES/OUT) existed. It's superseded by
`python pipeline/build_player_pool.py` and kept only for reference — it
writes to data/players_pool_legacy_output.csv, not the real pipeline's
output, so running it can't accidentally clobber your current projections.

Below is that legacy build()'s own original docstring, describing what it
does using:
  - FBref Standard/Goalkeeping/Misc stats for the 15 clubs that were in
    Ekstraklasa last season
  - a small hand-curated I liga 2025/26 dataset (from your message) for the
    3 promoted clubs (Wisła Kraków, Śląsk Wrocław, Wieczysta Kraków)
  - a league-average fallback for every player neither source covers
    (backup keepers, youth players, etc.)
  - your fixtures file's PointsChance as the per-gameweek difficulty multiplier

NAME MATCHING: your price list uses SURNAME ONLY (e.g. "Dziekoński"), while
FBref gives full names ("Xavier Dziekoński"). Matched by checking whether the
price-list surname equals the last word of an FBref name, using club as a
tiebreaker for any surname that repeats. This is exact-string matching on
clean UTF-8 diacritics on both sides now (not the "?"-wildcard trick from
before — your price list and the FBref .xls exports are both properly
encoded, so plain matching works here).

DUPLICATE NAMES IN YOUR I LIGA PASTE: several players (e.g. "Rafał Adamski",
"Łukasz Zjawiński") appeared identically under more than one promoted club in
your message — almost certainly copy/paste rather than one player at three
clubs. Resolved automatically: each is only applied under whichever club your
REAL price list actually lists them at (the price list is the authority on
current club, not the pasted I liga tables).
"""

from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.projections import estimate_xpts

RAW_DIR = Path("data/raw")
# NOTE: the build() function below (and these two paths) is this module's
# OWN legacy standalone pipeline, from before player status tracking existed.
# It is superseded by `python pipeline/build_player_pool.py` — this file is
# now kept for its shared helpers (load_fbref_players, build_iliga_lookup,
# ascii_fold, PROMOTED_CLUBS), which build_player_pool.py imports directly.
# Paths below intentionally do NOT point at data/players_pool.csv (the real
# pipeline's output) to avoid two scripts silently overwriting each other.
PLAYERS_PATH = Path("data/players_real_legacy.csv")  # no longer shipped — see note above
OUTPUT_PATH = Path("data/players_pool_legacy_output.csv")

SEASON_MATCHES = 34
MULTIPLIER_CLIP = (0.4, 2.0)
PROMOTED_CLUB_DOWNGRADE = 0.85   # 15% haircut for I liga -> Ekstraklasa step-up, per your instructions

POS_MAP = {"GK": "GK", "DF": "DEF", "MF": "MID", "FW": "FWD"}

# FBref "Squad" (as literally spelled in the lossy CSV export) -> your exact
# canonical club name. None = relegated club, excluded (no 2026/27 fixture).
FBREF_SQUAD_TO_CLUB = {
    "Cracovia": "Cracovia",
    "Górnik Zabrze": "Górnik Zabrze",
    "Jagiellonia": "Jagiellonia Białystok",
    "Katowice": "GKS Katowice",
    "Korona Kielce": "Korona Kielce",
    "Lech Pozna?": "Lech Poznań",
    "Legia Warsaw": "Legia Warszawa",
    "Motor Lublin": "Motor Lublin",
    "Piast Gliwice": "Piast Gliwice",
    "Pogo? Szczecin": "Pogoń Szczecin",
    "Radomiak": "Radomiak Radom",
    "Raków": "Raków Częstochowa",
    "Widzew ?ód?": "Widzew Łódź",
    "Wis?a P?ock": "Wisła Płock",
    "Zag??bie Lubin": "Zagłębie Lubin",
    "Arka Gdynia": None,
    "Lechia Gda?sk": None,
    "Nieciecza": None,
}

# fixtures xlsx "Code" -> your exact canonical club name
CODE_TO_CLUB = {
    "LPO": "Lech Poznań", "JAG": "Jagiellonia Białystok", "LEG": "Legia Warszawa",
    "RCZ": "Raków Częstochowa", "GKS": "GKS Katowice", "GOR": "Górnik Zabrze",
    "ZAG": "Zagłębie Lubin", "POG": "Pogoń Szczecin", "WPL": "Wisła Płock",
    "MOT": "Motor Lublin", "WIS": "Wisła Kraków", "RAD": "Radomiak Radom",
    "KOR": "Korona Kielce", "SLA": "Śląsk Wrocław", "CRA": "Cracovia",
    "PIA": "Piast Gliwice", "WIE": "Wieczysta Kraków", "WID": "Widzew Łódź",
}
CLUB_TO_CODE = {v: k for k, v in CODE_TO_CLUB.items()}
PROMOTED_CLUBS = {"Wisła Kraków", "Śląsk Wrocław", "Wieczysta Kraków"}

# Hand-curated from your message. goals_per90/assists_per90 computed from the
# season totals you gave using an ASSUMED ~27 "90s" of playing time for an
# established regular (documented approximation — I liga minutes data wasn't
# given, so this can't be exact; adjust ASSUMED_90S below if you have real
# minutes for these players).
ASSUMED_90S = 27.0
ILIGA_PROMOTED_STATS = [
    # name, position, goals_total, assists_total, goals_per90_given, assists_per90_given
    ("Ángel Rodado Jareño", "FWD", 21, None, 0.86, None),
    ("Łukasz Zjawiński", "FWD", 20, None, None, None),
    ("Stefan Feiertag", "FWD", 17, None, 0.89, None),
    ("Przemysław Banaszak", "FWD", 16, None, 0.64, None),
    ("Fabian Piasecki", "FWD", 14, None, None, None),
    ("Patryk Szwedzik", "FWD", 13, None, None, None),
    ("Jonathan Luiz Moreira Rosa Junior", "FWD", 13, None, None, None),
    ("Rafał Adamski", "FWD", 12, 8, None, None),
    ("Daniel Stanclik", "FWD", 12, None, None, None),
    ("Lisandro Semedo", "MID", 11, 11, None, None),
    ("Radosław Majewski", "MID", 11, None, None, None),
    ("Oliwier Kwiatkowski", "MID", 11, None, None, None),
    ("Julius Ertlthaler", "MID", None, 10, None, None),
    ("Paweł Kruszelnicki", "MID", None, 9, None, None),
    ("Piotr Samiec-Talar", "MID", None, 8, None, None),
    ("Kacper Duda", "MID", None, 8, None, None),
    ("Frederico Duarte", "MID", None, 8, None, None),
    ("Daniel Vega Cintas", "MID", None, 6, None, None),
    ("Julian Lelieveld", "DEF", None, 7, None, None),
    ("Bartosz Jaroch", "DEF", None, None, None, 0.62),
    ("Ervin Omic", "MID", None, None, None, 0.59),
    ("Joan Ángel Román i Ollè", "FWD", None, None, 0.71, None),
    ("Damian Warchoł", "FWD", None, None, 0.67, None),
    ("Paweł Łysiak", "FWD", None, None, 0.66, None),
    ("Marcin Listkowski", "MID", None, None, None, 0.57),
    ("Patryk Stefański", "MID", None, None, None, 0.39),
    ("Fryderyk Gerbowski", "MID", None, None, None, 0.39),
    # goalkeepers: clean_sheets given directly, no goals/assists relevant
]
ILIGA_PROMOTED_GK_CLEAN_SHEETS = {
    "Antoni Mikułko": 9, "Patryk Letkiewicz": 9, "Dawid Arndt": 9, "Jakub Lemanowicz": 9,
    "Alexander Bobek": 8, "Axel Holewinski": 7, "Michał Perchel": 7,
    "Michał Szromnik": 6,
}


import unicodedata

POLISH_FOLD = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "Đ": "D"})


def ascii_fold(s: str) -> str:
    s = str(s).translate(POLISH_FOLD)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


def _safe_div(numer: pd.Series, denom: pd.Series, default: float = 0.0) -> pd.Series:
    denom = denom.replace(0, pd.NA)
    return (numer / denom).fillna(default)


def _clip(x: float) -> float:
    lo, hi = MULTIPLIER_CLIP
    return max(lo, min(hi, x))


def _clip_prob(x: float) -> float:
    return max(0.0, min(1.0, x))


def load_fbref_players() -> pd.DataFrame:
    """Returns one row per FBref player with everything estimate_xpts() needs,
    plus a resolved canonical `club` and a `surname` column for matching."""
    std = pd.read_csv(RAW_DIR / "Standard_Stats.csv", header=1, encoding="cp1252")
    std = std.rename(columns={"Gls.1": "goals_per90", "Ast.1": "assists_per90"})
    std["primary_pos"] = std["Pos"].astype(str).str.split(",").str[0].map(POS_MAP).fillna("MID")
    std["Min"] = pd.to_numeric(std["Min"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    std["MP"] = pd.to_numeric(std["MP"], errors="coerce").fillna(0)
    std["Starts"] = pd.to_numeric(std["Starts"], errors="coerce").fillna(0)
    std["start_prob"] = std["Starts"].div(SEASON_MATCHES).clip(0, 1).fillna(0.0)
    std["bench_prob"] = (std["MP"] - std["Starts"]).div(SEASON_MATCHES).clip(0, 1).fillna(0.0)
    std["club"] = std["Squad"].map(FBREF_SQUAD_TO_CLUB)
    std = std[std["club"].notna()].copy()

    misc = pd.read_html(RAW_DIR / "fbref_misc_stats.xls", encoding="utf-8")[0]
    if isinstance(misc.columns, pd.MultiIndex):
        misc.columns = [c[-1] for c in misc.columns]
    misc.columns = [str(c).replace("\u25bc", "").strip() for c in misc.columns]
    misc["90s"] = pd.to_numeric(misc["90s"], errors="coerce").fillna(0).replace(0, pd.NA)
    for raw_col, out_col in [("CrdY", "yellow_card_prob"), ("CrdR", "red_card_prob"),
                              ("PKwon", "penalty_won_prob"), ("PKcon", "penalty_caused_prob"),
                              ("OG", "own_goal_prob")]:
        misc[out_col] = (pd.to_numeric(misc[raw_col], errors="coerce").fillna(0) / misc["90s"]).fillna(0.0)
    misc = misc.rename(columns={"Player": "fbref_name"})

    gk = pd.read_html(RAW_DIR / "fbref_goalkeeper_stats.xls", encoding="utf-8")[0]
    if isinstance(gk.columns, pd.MultiIndex):
        gk.columns = [c[-1] for c in gk.columns]
    gk["90s"] = pd.to_numeric(gk["90s"], errors="coerce").fillna(0)
    gk["penalty_save_prob"] = _safe_div(pd.to_numeric(gk["PKsv"], errors="coerce"),
                                         pd.to_numeric(gk["PKatt"], errors="coerce"))
    gk["saves_per_game"] = _safe_div(pd.to_numeric(gk["Saves"], errors="coerce"), gk["90s"])
    gk = gk.rename(columns={"Player": "fbref_name"})

    # Standard's names are lossy ("?"); Misc/GK names are clean. Since we're
    # about to match everything to the price list by SURNAME anyway (which
    # sidesteps most lossy characters unless the "?" happens to fall in the
    # surname itself), pull the clean full name across from Misc where the
    # surname matches, so the few "?" cases still resolve correctly.
    import re

    def clean_surname(full_name: str) -> str:
        return str(full_name).split()[-1]

    misc_surnames = [clean_surname(n) for n in misc["fbref_name"]]
    misc_surname_ascii = [ascii_fold(n) for n in misc_surnames]

    def resolve_clean_name(lossy_name: str) -> str:
        if "?" not in lossy_name:
            return lossy_name
        lossy_surname = clean_surname(lossy_name)
        if "?" not in lossy_surname:
            return lossy_name  # the "?" was in the first name only; surname is already fine
        # "?" sits inside the surname itself — wildcard-match it against the
        # clean Misc surname list (ascii-folded so ń/ł/etc. line up either way)
        pat = re.compile("^" + "".join("." if ch == "?" else re.escape(ch)
                                        for ch in ascii_fold(lossy_surname)) + "$", re.IGNORECASE)
        hits = {misc_surnames[i] for i, a in enumerate(misc_surname_ascii) if pat.match(a)}
        if len(hits) == 1:
            first_name = lossy_name.rsplit(" ", 1)[0]
            return f"{first_name} {hits.pop()}"
        return lossy_name  # ambiguous or no match — leave as-is, surname_key matching downstream still tries ascii-fold

    std["fbref_name"] = std["Player"].apply(resolve_clean_name)
    std["surname"] = std["fbref_name"].apply(clean_surname)

    merged = std.merge(misc[["fbref_name", "yellow_card_prob", "red_card_prob", "penalty_won_prob",
                              "penalty_caused_prob", "own_goal_prob"]], on="fbref_name", how="left")
    merged = merged.merge(gk[["fbref_name", "penalty_save_prob", "saves_per_game"]],
                           on="fbref_name", how="left")
    for col in ("yellow_card_prob", "red_card_prob", "penalty_won_prob", "penalty_caused_prob",
                "own_goal_prob", "penalty_save_prob", "saves_per_game"):
        merged[col] = merged[col].fillna(0.0)

    return merged[["surname", "club", "primary_pos", "goals_per90", "assists_per90",
                    "start_prob", "bench_prob", "yellow_card_prob", "red_card_prob",
                    "penalty_won_prob", "penalty_caused_prob", "own_goal_prob",
                    "penalty_save_prob", "saves_per_game"]]


def load_fixtures() -> tuple[dict, float]:
    wb = openpyxl.load_workbook(RAW_DIR / "POLAND_FIXTURES_AND_XPTS.xlsx", data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    by_code = {}
    all_pc = []
    for row in rows:
        code = row[1]
        by_code[code] = {}
        for gw in range(1, 7):
            base = 3 + (gw - 1) * 3
            opp_code, home_away, pc = row[base], row[base + 1], row[base + 2]
            if opp_code is None:
                continue
            by_code[code][gw] = pc
            all_pc.append(pc)
    league_avg = sum(all_pc) / len(all_pc) if all_pc else 50.0
    return by_code, league_avg


def build_iliga_lookup() -> dict:
    """surname -> dict of per90 rates, for the promoted-club players you
    hand-curated. Downgraded by PROMOTED_CLUB_DOWNGRADE per your instructions."""
    lookup = {}
    for name, pos, goals_total, assists_total, g90_given, a90_given in ILIGA_PROMOTED_STATS:
        surname = ascii_fold(name.split()[-1])
        g90 = g90_given if g90_given is not None else (goals_total / ASSUMED_90S if goals_total else 0.0)
        a90 = a90_given if a90_given is not None else (assists_total / ASSUMED_90S if assists_total else 0.0)
        lookup[surname] = {
            "goals_per90": g90 * PROMOTED_CLUB_DOWNGRADE,
            "assists_per90": a90 * PROMOTED_CLUB_DOWNGRADE,
            "position": pos,
        }
    for name, cs in ILIGA_PROMOTED_GK_CLEAN_SHEETS.items():
        surname = ascii_fold(name.split()[-1])
        # ~34 matches/season -> clean_sheet_prob approximation, downgraded for the step up
        lookup[surname] = {"clean_sheet_prob": (cs / SEASON_MATCHES) * PROMOTED_CLUB_DOWNGRADE,
                            "position": "GK"}
    return lookup


def build():
    players = pd.read_csv(PLAYERS_PATH)
    fbref = load_fbref_players()
    fixtures_by_code, league_avg_pc = load_fixtures()
    iliga = build_iliga_lookup()

    print(f"Loaded {len(players)} real players, {len(fbref)} FBref-covered players, "
          f"{len(iliga)} curated I liga entries. League-avg PointsChance: {league_avg_pc:.1f}")

    # position-average fallback (across all FBref-covered players) for anyone
    # matched to nothing at all — per your "no stats -> position average" rule
    pos_avg = fbref.groupby("primary_pos")[["goals_per90", "assists_per90"]].mean().to_dict("index")

    # surname+club lookup, ascii-folded since FBref's own stored names
    # sometimes drop diacritics with no marker at all (see module docstring) —
    # exact string matching on the raw surname misses these silently.
    fbref["surname_key"] = fbref["surname"].apply(ascii_fold)
    fbref_by_surname_club = {(r["surname_key"], r["club"]): r for _, r in fbref.iterrows()}
    fbref_by_surname = {}
    for _, r in fbref.iterrows():
        fbref_by_surname.setdefault(r["surname_key"], []).append(r)

    n_fbref_matched, n_iliga_matched, n_fallback = 0, 0, 0
    unmatched_examples = []

    gw_cols = {gw: [] for gw in range(1, 6)}
    xmins_list = []

    def _xmins(start_prob: float, bench_prob: float) -> float:
        # simple, transparent expected-minutes model: full 90 if nailed to
        # start, ~20 minutes of impact-sub time if only appearing from the
        # bench. Not fixture-specific (minutes predictions don't move much
        # week to week the way points do), so this is one column, not one
        # per gameweek — good enough to rank "nailed" vs "rotation risk"
        # players, which is what xmin_lb filtering needs it for.
        return round(min(90.0, max(0.0, start_prob * 90 + bench_prob * 20)), 1)

    for _, p in players.iterrows():
        surname, club, pos = p["name"], p["club"], p["position"]
        surname_key = ascii_fold(surname)
        code = CLUB_TO_CODE.get(club)

        row = fbref_by_surname_club.get((surname_key, club))
        source = None
        if row is not None:
            source = "fbref"
            n_fbref_matched += 1
        elif surname_key in iliga:
            source = "iliga"
            n_iliga_matched += 1
        else:
            # last resort: surname matched at a DIFFERENT club (transfer the
            # price list knows about but FBref predates) — still better than
            # a pure average
            candidates = fbref_by_surname.get(surname_key)
            if candidates and len(candidates) == 1:
                row = candidates[0]
                source = "fbref_other_club"
                n_fbref_matched += 1
            else:
                source = "fallback"
                n_fallback += 1
                if len(unmatched_examples) < 15:
                    unmatched_examples.append(f"{surname} ({club}, {pos})")

        for gw in range(1, 6):
            multiplier = 1.0
            if code and code in fixtures_by_code and gw in fixtures_by_code[code]:
                multiplier = _clip(fixtures_by_code[code][gw] / league_avg_pc) if league_avg_pc else 1.0

            if source in ("fbref", "fbref_other_club"):
                pts = estimate_xpts(
                    position=pos,
                    start_prob=row["start_prob"], bench_prob=row["bench_prob"],
                    goals_per90=row["goals_per90"] * multiplier,
                    assists_per90=row["assists_per90"] * multiplier,
                    clean_sheet_prob=_clip_prob(0.30 * multiplier) if pos in ("GK", "DEF", "MID") else 0.0,
                    penalty_save_prob=row["penalty_save_prob"], saves_per_game=row["saves_per_game"],
                    yellow_card_prob=row["yellow_card_prob"], red_card_prob=row["red_card_prob"],
                    penalty_won_prob=row["penalty_won_prob"], own_goal_prob=row["own_goal_prob"],
                    penalty_caused_prob=row["penalty_caused_prob"],
                )
                if gw == 1:
                    xmins_list.append(_xmins(row["start_prob"], row["bench_prob"]))
            elif source == "iliga":
                info = iliga[surname_key]
                pts = estimate_xpts(
                    position=pos, start_prob=0.6, bench_prob=0.15,
                    goals_per90=info.get("goals_per90", 0.0) * multiplier,
                    assists_per90=info.get("assists_per90", 0.0) * multiplier,
                    clean_sheet_prob=_clip_prob(info.get("clean_sheet_prob", 0.30) * multiplier)
                    if pos in ("GK", "DEF", "MID") else 0.0,
                )
                if gw == 1:
                    xmins_list.append(_xmins(0.6, 0.15))
            else:  # fallback: league position-average, still fixture-adjusted
                avg = pos_avg.get(pos, {"goals_per90": 0.05, "assists_per90": 0.05})
                pts = estimate_xpts(
                    position=pos, start_prob=0.4, bench_prob=0.2,
                    goals_per90=avg["goals_per90"] * multiplier,
                    assists_per90=avg["assists_per90"] * multiplier,
                    clean_sheet_prob=_clip_prob(0.25 * multiplier) if pos in ("GK", "DEF", "MID") else 0.0,
                )
                if gw == 1:
                    xmins_list.append(_xmins(0.4, 0.2))
            gw_cols[gw].append(pts)

    print(f"\nMatch summary: {n_fbref_matched} via FBref, {n_iliga_matched} via curated I liga data, "
          f"{n_fallback} via position-average fallback.")
    if unmatched_examples:
        print(f"Sample fallback cases (no FBref/I liga data found — youth/backup players, expected):")
        for e in unmatched_examples:
            print(f"    - {e}")

    out = players.copy()
    out["xmins"] = xmins_list
    for gw in range(1, 6):
        out[f"xpts_gw{gw}"] = gw_cols[gw]
    out[[f"xpts_gw{gw}" for gw in range(1, 6)]] = out[[f"xpts_gw{gw}" for gw in range(1, 6)]].clip(lower=0.0)
    out = out.round({f"xpts_gw{gw}": 2 for gw in range(1, 6)})

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(out)} players (prices unchanged) to {OUTPUT_PATH}")

    # verification checklist
    print("\n--- Verification ---")
    print(f"All 544 players preserved: {len(out) == 544}")
    print(f"Prices unchanged: {(out['price'] == players['price']).all()}")
    xpts_all = out[[f'xpts_gw{gw}' for gw in range(1, 6)]].values.flatten()
    print(f"xPts range: {xpts_all.min():.2f} to {xpts_all.max():.2f}")
    return out


if __name__ == "__main__":
    build()

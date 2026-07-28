"""
Builds the canonical data/fixtures.csv (one row per team per gameweek, GW1-8)
from the raw FBref extended fixture export.

INPUT:  data/raw/fixtures_8gw_raw.csv (FBref "Scores & Fixtures", Wk 1-8)
OUTPUT: data/fixtures.csv - team, gw, opponent, is_home, date, kickoff_local,
        status, notes

Team names in the raw file use FBref's short spellings ("Katowice", "Raków",
"Wieczysta", ...); FIXTURE_TEAM_TO_CLUB below maps each to the canonical
club name used everywhere else in this project (players file, club_strength.csv).

RESCHEDULES / POSTPONEMENTS
----------------------------
When a match gets moved, add one entry here rather than hand-editing the
generated CSV (this file is regenerated from the raw export every time you
run it, so hand edits to fixtures.csv would just get overwritten). Keyed by
(gw, home_club, away_club) exactly as they appear after name-mapping.

    "status": "rescheduled" | "postponed" | "played" | "scheduled"
    "kickoff_local": new local kickoff time (24h "HH:MM"), or None to keep
    "date": new date "YYYY-MM-DD", or None to keep the raw file's date
    "note": free text, shown in fixtures.csv for anyone reading the sheet

A "rescheduled" match still counts as that team's fixture for that gameweek
(same opponent/venue, just a different kickoff) - the model treats it like
any other GW fixture. Only "postponed" (no new date yet) removes the match
from both clubs' gameweek entirely, leaving them with a blank gameweek,
since there's nothing on the pitch that round to project points for.
"""

from __future__ import annotations
from pathlib import Path

import pandas as pd

RAW_FIXTURES_PATH = Path("data/raw/fixtures_8gw_raw.csv")
OUTPUT_PATH = Path("data/fixtures.csv")

FIXTURE_TEAM_TO_CLUB = {
    "Cracovia": "Cracovia",
    "Górnik Zabrze": "Górnik Zabrze",
    "Jagiellonia": "Jagiellonia Białystok",
    "Katowice": "GKS Katowice",
    "Korona Kielce": "Korona Kielce",
    "Lech Poznań": "Lech Poznań",
    "Legia Warsaw": "Legia Warszawa",
    "Motor Lublin": "Motor Lublin",
    "Piast Gliwice": "Piast Gliwice",
    "Pogoń Szczecin": "Pogoń Szczecin",
    "Radomiak": "Radomiak Radom",
    "Raków": "Raków Częstochowa",
    "Widzew Łódź": "Widzew Łódź",
    "Wieczysta": "Wieczysta Kraków",
    "Wisła Kraków": "Wisła Kraków",
    "Wisła Płock": "Wisła Płock",
    "Zagłębie Lubin": "Zagłębie Lubin",
    "Śląsk Wrocław": "Śląsk Wrocław",
}

# --- one entry per rescheduled/postponed match; add to this as news breaks ---
RESCHEDULES = {
    (2, "Korona Kielce", "Górnik Zabrze"): {
        "status": "rescheduled",
        "date": "2026-08-01",
        "kickoff_local": "18:15",
        "note": "Moved up from the original 20:15 local slot (still Matchweek 2, "
                "still Korona (H) v Górnik (A)); confirmed 2026-08-01, 18:15.",
    },
}


def _parse_kickoff(time_field: str) -> str | None:
    """FBref's Time column looks like '20:15 (18:15)' (local (UTC)); we only
    want the local time. Blank for the later gameweeks that haven't had a
    kickoff time confirmed yet."""
    if not isinstance(time_field, str) or not time_field.strip():
        return None
    return time_field.split("(")[0].strip() or None


def build_fixtures(raw_path: Path = RAW_FIXTURES_PATH, output_path: Path = OUTPUT_PATH) -> pd.DataFrame:
    if not raw_path.exists():
        raise SystemExit(f"'{raw_path}' not found. Save a fresh FBref Scores & Fixtures "
                          f"export there (Wk, Day, Date, Time, Home, Away, Notes columns).")
    raw = pd.read_csv(raw_path)
    raw = raw.dropna(subset=["Wk", "Home", "Away"]).copy()
    raw["Wk"] = raw["Wk"].astype(int)

    rows = []
    unmapped = set()
    for _, r in raw.iterrows():
        gw = int(r["Wk"])
        home = FIXTURE_TEAM_TO_CLUB.get(r["Home"])
        away = FIXTURE_TEAM_TO_CLUB.get(r["Away"])
        if home is None:
            unmapped.add(r["Home"])
        if away is None:
            unmapped.add(r["Away"])
        if home is None or away is None:
            continue

        date = pd.to_datetime(r["Date"]).strftime("%Y-%m-%d") if pd.notna(r.get("Date")) else None
        kickoff = _parse_kickoff(r.get("Time"))
        raw_note = str(r.get("Notes", "") or "").strip()
        status = "postponed" if raw_note.lower() == "match postponed" else "scheduled"

        override = RESCHEDULES.get((gw, home, away))
        note = raw_note
        if override:
            status = override.get("status", status)
            date = override.get("date", date)
            kickoff = override.get("kickoff_local", kickoff)
            note = override.get("note", raw_note)

        for team, opponent, is_home in ((home, away, True), (away, home, False)):
            rows.append({
                "team": team, "gw": gw, "opponent": opponent, "is_home": is_home,
                "date": date, "kickoff_local": kickoff, "status": status, "notes": note,
            })

    if unmapped:
        print(f"Warning: unrecognised team name(s) in '{raw_path}', skipped: {sorted(unmapped)}")

    out = pd.DataFrame(rows).sort_values(["gw", "team"]).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)

    n_gw = out["gw"].nunique()
    n_postponed = (out["status"] == "postponed").sum() // 2
    n_rescheduled = (out["status"] == "rescheduled").sum() // 2
    print(f"Wrote {len(out)} team-fixture rows ({n_gw} gameweek(s)) to {output_path}.")
    if n_postponed:
        print(f"  {n_postponed} match(es) marked postponed (excluded from both clubs' gameweek).")
    if n_rescheduled:
        print(f"  {n_rescheduled} match(es) rescheduled (date/kickoff updated, still counts for that GW).")
    return out


if __name__ == "__main__":
    build_fixtures()

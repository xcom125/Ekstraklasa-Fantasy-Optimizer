"""
One-off adapter: turns the "Club Strength & Next Match Predictions" export
(pasted as a .md text dump, not a CSV — StatsUltra's page copies as loose
lines of text) into the two raw CSVs pipeline/parse_statsultra.py already
knows how to consume:

    data/raw/statsultra_club_strength_raw.csv
    data/raw/statsultra_next_round_raw.csv

Why a separate adapter instead of teaching parse_statsultra.py to read .md
directly: parse_statsultra.py's job (raw CSV -> club_strength.csv +
fixtures_statsultra_probs.csv) is unchanged and still correct — the only new
problem is that this week's snapshot arrived as text instead of CSV. Keeping
that as a thin translation step here means parse_statsultra.py never needs
touching, whatever shape next week's paste happens to arrive in.

MATCH-DATE -> GAMEWEEK RESOLUTION
----------------------------------
The match-predictions block has no gameweek number, just a date ("27 Jul",
"31 Jul", ...). Each date is resolved to a gw by looking up the fixture in
data/fixtures.csv for that exact (home_team, away_team) pair — that file is
the canonical GW1-8 calendar (built from FBref via build_fixtures.py). One
row in this export (Zagłębie Lubin vs Piast Gliwice, 27 Jul) turns out to
already be GW1's fixture — re-confirms the existing gw1 win_prob rather than
adding a new one — while the other eight are GW2. Rows are grouped by the gw
they resolve to and written out per-gw, so build_fixture_probs() (which
takes a single --gw) is called once per group and the results are merged
into fixtures_statsultra_probs.csv without clobbering gameweeks this export
doesn't cover.

Team names in the export are already the full canonical Polish spelling
(matching data/fixtures.csv), so no TEAM_NAME_MAP lookup is strictly needed
here — but every team name is still passed through canonical_team() so a
future week's paste with a different spelling doesn't silently corrupt the
join.
"""

from __future__ import annotations
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.team_names import canonical_team
from pipeline import parse_statsultra as su

RAW_MD_PATH = Path("data/raw/statsultra_export_gw2.md")
FIXTURES_PATH = Path("data/fixtures.csv")

CLUB_STRENGTH_RAW_OUT = su.RAW_STRENGTH_PATH
NEXT_ROUND_RAW_OUT = su.RAW_FIXTURES_PATH
CLUB_STRENGTH_OUT = su.CLUB_STRENGTH_OUT
FIXTURES_PROBS_OUT = su.FIXTURES_PROBS_OUT

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _lines(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]


def parse_club_strength(path: Path = RAW_MD_PATH) -> pd.DataFrame:
    """Table 1: world_rank / trend / logo / team name / "Ekstraklasa" /
    strength / offence / defence, one club per ~9 text lines."""
    lines = _lines(path)
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("WORLD RANK"))
    except StopIteration:
        raise SystemExit(f"'{path}': couldn't find the 'WORLD RANK / TREND / ...' header line.")
    try:
        end = next(i for i, ln in enumerate(lines) if ln.startswith("Upcoming Ekstraklasa"))
    except StopIteration:
        end = len(lines)

    body = [ln for ln in lines[start + 1:end] if ln]
    # rank + trend arrive on one line, tab-separated, e.g. "103\t▼ 2" or
    # "283\t—" — trend is unused (informational only), rank is the only
    # part that matters and only as a block delimiter.
    rank_re = re.compile(r"^(\d+)\t")
    rows = []
    i = 0
    while i < len(body):
        m = rank_re.match(body[i])
        if not m:
            i += 1
            continue
        world_rank = int(m.group(1))
        # body[i+1] = "<Club> Logo", body[i+2] = real (diacritic) team name,
        # body[i+3] = "Ekstraklasa" league label
        team_name = body[i + 2].strip()
        strength = float(body[i + 4])
        offence = float(body[i + 5])
        defence = float(body[i + 6])
        rows.append({
            "club": canonical_team(team_name),
            "world_rank": world_rank,
            "team_strength": strength,
            "offence": offence,
            "defence": defence,
        })
        i += 7

    if len(rows) != 18:
        raise SystemExit(f"'{path}': parsed {len(rows)} clubs, expected 18 — check the format.")
    return pd.DataFrame(rows)


def parse_match_predictions(path: Path = RAW_MD_PATH) -> pd.DataFrame:
    """Table 2: one block per match —
        <date>
        <home club> logo
        <HOME_CODE>
        <home strength>
        <hw%><draw%><aw%>   (concatenated, e.g. "42.0%26.5%31.5%")
        <AWAY_CODE>
        <away strength>
        <away club> logo
        Average Strength: <x>
        [Top Game]              (optional)
    """
    lines = _lines(path)
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Upcoming Ekstraklasa"))
    body = [ln for ln in lines[start + 1:] if ln]

    date_re = re.compile(r"^\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$")
    pct_re = re.compile(r"^(\d+\.\d+)%(\d+\.\d+)%(\d+\.\d+)%$")

    rows = []
    i = 0
    year = 2026  # 2026/27 Ekstraklasa season
    while i < len(body):
        if not date_re.match(body[i]):
            i += 1
            continue
        day, mon = body[i].split()
        match_date = f"{year}-{MONTHS[mon]:02d}-{int(day):02d}"
        home_logo_line = body[i + 1]
        home_team = home_logo_line.removesuffix(" logo").removesuffix(" Logo").strip()
        # body[i+2] = home code (unused — full names already parsed above)
        home_strength = float(body[i + 3])
        m = pct_re.match(body[i + 4])
        if not m:
            raise SystemExit(f"'{path}': expected 'HW%DRAW%AW%' at line {i + 4}, got {body[i + 4]!r}.")
        home_win_pct, draw_pct, away_win_pct = (float(x) for x in m.groups())
        # body[i+5] = away code (unused)
        away_strength = float(body[i + 6])
        away_logo_line = body[i + 7]
        away_team = away_logo_line.removesuffix(" logo").removesuffix(" Logo").strip()
        j = i + 8
        if j < len(body) and body[j].startswith("Average Strength"):
            j += 1
        top_game = False
        if j < len(body) and body[j] == "Top Game":
            top_game = True
            j += 1

        rows.append({
            "date": match_date,
            "home_team": canonical_team(home_team),
            "home_strength": home_strength,
            "home_win_pct": home_win_pct,
            "draw_pct": draw_pct,
            "away_win_pct": away_win_pct,
            "away_team": canonical_team(away_team),
            "away_strength": away_strength,
            "top_game": top_game,
        })
        i = j

    if not rows:
        raise SystemExit(f"'{path}': parsed 0 matches — check the format.")
    return pd.DataFrame(rows)


def _resolve_gameweeks(matches: pd.DataFrame, fixtures_path: Path = FIXTURES_PATH) -> pd.DataFrame:
    """Looks up each (home_team, away_team) pair in fixtures.csv to find
    which gw it belongs to, so a stale/reconfirmed earlier-gw row (like the
    Zagłębie-Piast match, already GW1) doesn't get mislabeled as the new
    round."""
    fx = pd.read_csv(fixtures_path)
    lookup = {(r["team"], r["opponent"]): int(r["gw"]) for _, r in fx.iterrows() if r["is_home"]}
    gws = []
    for _, r in matches.iterrows():
        gw = lookup.get((r["home_team"], r["away_team"]))
        if gw is None:
            raise SystemExit(
                f"'{FIXTURES_PATH}': no fixture found for {r['home_team']} vs {r['away_team']} "
                f"— run pipeline/build_fixtures.py first, or check team name spelling."
            )
        gws.append(gw)
    matches = matches.copy()
    matches["gw"] = gws
    return matches


def run(md_path: Path = RAW_MD_PATH) -> None:
    strength = parse_club_strength(md_path)
    CLUB_STRENGTH_RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    strength.to_csv(CLUB_STRENGTH_RAW_OUT, index=False)
    print(f"Wrote {len(strength)} clubs to {CLUB_STRENGTH_RAW_OUT}")

    matches = parse_match_predictions(md_path)
    matches = _resolve_gameweeks(matches)
    print(f"Parsed {len(matches)} match predictions, resolved to gw(s): "
          f"{sorted(matches['gw'].unique().tolist())}")

    # re-derive club_strength.csv / fixtures_statsultra_probs.csv via the
    # existing (untouched) parse_statsultra.py, one gw group at a time so a
    # reconfirmed earlier gw (e.g. gw1) doesn't get relabeled, and gw2's new
    # rows get APPENDED to fixtures_statsultra_probs.csv rather than
    # clobbering any other gw already in it.
    su.build_club_strength(raw_path=CLUB_STRENGTH_RAW_OUT, out_path=CLUB_STRENGTH_OUT)

    existing = pd.read_csv(FIXTURES_PROBS_OUT) if FIXTURES_PROBS_OUT.exists() else pd.DataFrame(
        columns=["team", "gw", "opponent", "is_home", "win_prob", "draw_prob", "date"])

    all_new = []
    for gw, group in matches.groupby("gw"):
        group_cols = ["date", "home_team", "home_strength", "home_win_pct",
                      "draw_pct", "away_win_pct", "away_team", "away_strength", "top_game"]
        group[group_cols].to_csv(NEXT_ROUND_RAW_OUT, index=False)
        new_rows = su.build_fixture_probs(raw_path=NEXT_ROUND_RAW_OUT, out_path=FIXTURES_PROBS_OUT, gw=int(gw))
        all_new.append(new_rows)

    merged_new = pd.concat(all_new, ignore_index=True)
    gws_touched = set(merged_new["gw"].unique().tolist())
    kept_old = existing[~existing["gw"].isin(gws_touched)] if len(existing) else existing
    final = pd.concat([kept_old, merged_new], ignore_index=True).sort_values(["gw", "team"]).reset_index(drop=True)
    final.to_csv(FIXTURES_PROBS_OUT, index=False)
    print(f"Wrote {len(final)} team-fixture rows ({sorted(final['gw'].unique().tolist())}) to {FIXTURES_PROBS_OUT}")


if __name__ == "__main__":
    run()

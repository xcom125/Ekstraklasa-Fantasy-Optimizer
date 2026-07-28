"""
Quick lookup: given player names, prints their player_id (+ club/position/
price) from data/players_pool.csv — so you can copy IDs straight into
settings.json's "initial_squad" without scrolling through the CSV by hand.

Matching is case-insensitive and partial, so a surname is usually enough.

USAGE

  One-off, names as arguments (quote multi-word names):
      python find_player_ids.py "Ishak" "Wålemark" "Dziekoński"

  Your whole squad at once, one name per line in a text file:
      python find_player_ids.py --file my_squad.txt

  No arguments: prompts you to paste names one per line (blank line to finish).

If a name matches more than one player (common surname, or two spellings),
every match is printed with its full row so you can pick the right one.
If nothing matches, it's printed as "no match" — check spelling/diacritics
against data/players_pool.csv, or the player may be missing from the pool.
"""

from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

POOL_PATH = Path("data/players_pool.csv")


def _normalize(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def find_ids(names: list[str], pool_path: Path = POOL_PATH) -> None:
    if not pool_path.exists():
        raise SystemExit(f"'{pool_path}' not found — run pipeline/build_player_pool.py first.")
    df = pd.read_csv(pool_path)
    df["_key"] = df["name"].apply(_normalize)

    ids_found = []
    print()
    for raw_name in names:
        name = raw_name.strip()
        if not name:
            continue
        key = _normalize(name)
        matches = df[df["_key"].str.contains(key, na=False)]

        if len(matches) == 0:
            print(f"  '{name}' -> no match. Check spelling against {pool_path}.")
        elif len(matches) == 1:
            row = matches.iloc[0]
            print(f"  '{name}' -> id {int(row['player_id'])}  "
                  f"({row['name']}, {row['club']}, {row['position']}, {row['price']}M)")
            ids_found.append(int(row["player_id"]))
        else:
            print(f"  '{name}' -> {len(matches)} matches, pick the right one:")
            for _, row in matches.iterrows():
                print(f"       id {int(row['player_id'])}  "
                      f"({row['name']}, {row['club']}, {row['position']}, {row['price']}M)")

    if ids_found:
        print(f"\nUnambiguous matches as a settings.json-ready list:\n{ids_found}")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--file":
        if len(args) < 2:
            raise SystemExit("Usage: python find_player_ids.py --file my_squad.txt")
        text = Path(args[1]).read_text(encoding="utf-8")
        names = [line for line in text.splitlines() if line.strip()]
    elif args:
        names = args
    else:
        print("Paste your 15 player names, one per line. Blank line to finish:")
        names = []
        while True:
            line = input()
            if not line.strip():
                break
            names.append(line)

    find_ids(names)

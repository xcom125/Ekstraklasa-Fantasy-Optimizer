# 🇵🇱 Ekstraklasa Fantasy Optimizer

An open-source multi-week squad and transfer optimizer for **LOTTO Fantasy Ekstraklasa** in the spirit of the all mighty [open-fpl-solver](https://github.com/solioanalytics/open-fpl-solver), built with Python, Pandas, and PuLP (ILP solver). 

---

##  Quickstart

```bash
# 1. Clone & install
git clone [https://github.com/xcom125/Ekstraklasa-Fantasy-Optimizer.git](https://github.com/xcom125/Ekstraklasa-Fantasy-Optimizer.git)
cd Ekstraklasa-Fantasy-Optimizer
pip install -r requirements.txt

# 2. Run solver
python main.py run --settings settings.json

# 3. Build xPts player pool
python pipeline/build_fixtures.py
python pipeline/build_player_pool.py


##  Project Layout

Ekstraklasa-Fantasy-Optimizer/
├── pipeline/      # Data parsing & player pool generation
├── src/           # Single & multi-week ILP solver models
├── data/          # Clean CSVs (fixtures, player pool, club strength)
├── exports/       # Optimal squad picks & transfer plans
├── main.py        # Main CLI entrypoint
└── settings.json  # Weekly settings (squad IDs, ITB budget, FTs)

##  License: MIT License.

##  Acknowledgements & Open Source Credits
**[open-fpl-solver](https://github.com/solioanalytics/open-fpl-solver)** (Solio Analytics, Apache 2.0) — Structural inspiration for multi-period integer linear programming patterns in fantasy analytics.
* **CBC & HiGHS Solvers** — High-performance open-source linear programming engines (via PuLP).
* **StatsUltra, Sofascore & FBref** — Source data for club strength metrics, match predictions, and player performance statistics.

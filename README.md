# 🇵🇱 Ekstraklasa Fantasy Optimizer

An open-source, local integer linear programming (ILP) solver for **LOTTO Fantasy Ekstraklasa**, in the
spirit of [open-fpl-solver](https://github.com/solioanalytics/open-fpl-solver). 
Powered by **Python**, **Pandas**, and **PuLP** (utilizing the free, bundled **CBC** or **HiGHS** solver).

Optimizes squad selection, starting XIs, captaincy, and multi-gameweek transfer planning under official Ekstraklasa budget, position, and club limits.

---

## Quickstart

### 1. Clone the repository
```bash
git clone [https://github.com/xcom125/Ekstraklasa-Fantasy-Optimizer.git](https://github.com/xcom125/Ekstraklasa-Fantasy-Optimizer.git)
cd Ekstraklasa-Fantasy-Optimizer

2. Install dependencies (Python 3.10+)Bashpip install -r requirements.txt
3. Build player pool & run the data pipelineMerge player metrics, fixture difficulty, and historical season statistics:Bashpython pipeline/build_fixtures.py
python pipeline/build_player_pool.py
4. Run the OptimizerSolve for an $N$-gameweek horizon using your settings file (picks and transfer paths land in exports/):Bashpython main.py run --settings settings.json
⚙️ How It WorksData Engineering (pipeline/): Blends current form, fixture ratings, and historical per-90 metrics (including past season performance) using a multi-tier player matching engine.ILP Solver (src/): Models budget ($30\text{M}$ max), squad composition ($2\text{ GK} / 5\text{ DEF} / 5\text{ MID} / 3\text{ FWD}$), valid starting formations, club limits ($\le 3$ per club), and rolling free transfers / hit penalties across your target horizon.Solver Support: Runs out-of-the-box using PuLP's bundled CBC solver, with automatic detection and fallback for HiGHS (via highspy) for faster solves over longer horizons.🛠️ Project LayoutPlaintextEkstraklasa-Fantasy-Optimizer/
├── data/                  # Cleaned data (fixtures.csv, club_strength.csv, players_pool.csv)
│   └── raw/               # Raw underlying stats & exports
├── exports/               # Solver outputs and optimal horizon plans
├── pipeline/              # Data parsing, fixture parsing, and player pool builders
├── src/                   # Core ILP logic (single/multi-week solvers, config, projections)
├── main.py                # Main CLI entrypoint
├── settings.json          # Weekly user settings (initial squad, ITB budget, FTs)
├── settings.example.json  # Configuration template
└── requirements.txt       # Python dependencies
📋 Weekly WorkflowUpdate Data: Save fresh player status exports to data/raw/player_status_export.csv and updated fixtures to data/raw/fixtures_8gw_raw.csv.Rebuild Pool: Run the pipeline scripts:Bashpython pipeline/build_fixtures.py
python pipeline/build_player_pool.py
Configure Settings: Edit settings.json with your current team ID list (initial_squad), remaining budget (itb), and available free transfers (ft).Solve: Run python main.py run --settings settings.json.📄 LicenseThis project is open-source and free to use under the MIT License.

# Ekstraklasa Fantasy Optimizer

An open-source multi-week squad and transfer optimizer for **LOTTO Fantasy Ekstraklasa**, inspired by the excellent [open-fpl-solver](https://github.com/solioanalytics/open-fpl-solver). Built with **Python**, **Pandas**, and **PuLP**, it uses Integer Linear Programming (ILP) to optimize squad selection and transfer planning across multiple gameweeks.

---

##  Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/xcom125/Ekstraklasa-Fantasy-Optimizer.git
cd Ekstraklasa-Fantasy-Optimizer
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the optimizer

```bash
python main.py run --settings settings.json
```

---

## 📁 Project Structure

```text
Ekstraklasa-Fantasy-Optimizer/
├── pipeline/      # Data parsing & player pool generation
├── src/           # Single- and multi-week ILP optimization models
├── data/          # Clean datasets (fixtures, player pool, club strength)
├── exports/       # Optimized squads and transfer plans
├── main.py        # Main CLI entry point
└── settings.json  # User configuration (budget, squad, free transfers, etc.)
```

---

##  Features

- Multi-gameweek squad optimization
- Intelligent transfer planning
- Integer Linear Programming (ILP) optimization with PuLP
- Expected points (xPts) player pool generation
- Configurable solver settings via `settings.json`
- Exportable squad and transfer recommendations

---

##  Acknowledgements

This project builds upon and is inspired by several outstanding open-source projects and data providers:

- **[open-fpl-solver](https://github.com/solioanalytics/open-fpl-solver)** (Solio Analytics, Apache 2.0) — Inspiration for the multi-period Integer Linear Programming framework used for fantasy optimization.
- **CBC & HiGHS** — High-performance open-source optimization solvers used through PuLP.
- **StatsUltra, Sofascore & FBref** — Sources for club strength metrics, fixture difficulty, match predictions, and player performance statistics.

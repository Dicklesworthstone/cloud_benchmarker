# AGENTS.md — Cloud Benchmarker

Operational law for AI agents working in this repo. Read fully before touching anything.

## What this is

A FastAPI + Ansible tool that benchmarks cloud VPSes with `sysbench` (CPU, memory, fileio, mutex, threads), ingests results into SQLite through a single-worker scheduler, and serves raw/normalized scores, historical CSV, and Plotly dashboards. No formal releases; development on `main`.

## Layout

| Path | Role |
|---|---|
| `web_app/app/main.py` | App factory, lifespan (init_db + scheduler thread), dashboard at `/` |
| `web_app/app/routes/api_routes.py` | `/data/raw/`, `/data/overall/` (422 on bad `time_period`), `/benchmark_charts/`, `/benchmark_historical_csv/` — all sync `def` (threadpool offload is deliberate) |
| `web_app/app/utils/scheduler.py` | Playbook run, staleness gate (half the configured interval, epoch math), per-host ingest with isolation, guarded ticks |
| `web_app/app/database/data_models.py` | ORM + `METRIC_COLUMNS` — the canonical 5-metric contract shared with the playbook and scoring script |
| `web_app/app/database/init_db.py` | Engine (repo-root-anchored SQLite), `get_db` |
| `web_app/app/logger_config.py` | Repo-root-anchored rotating log + `old_logs/` |
| `web_app/app/chart.py` | Subscore figure (exact IP/metric dropdown matching) + charts page |
| `script_to_generate_overall_benchmark_scores_from_subscores.py` | Standalone scorer the playbook copies to `/tmp` (parser duplication with `scheduler.load_combined_results` is forced — do not "fix" without solving that) |
| `benchmark-playbook.yml` | 5 sysbench tests per host → per-host JSON → fetch → combine → scoring |
| `tests/` | 61 tests, 100% statement coverage; conftest isolates HOME + DB before any app import |

## Verify gates — ALL green before any close

```bash
source venv/bin/activate
python -m pytest tests/ -q
ruff check .
mypy web_app/app script_to_generate_overall_benchmark_scores_from_subscores.py
bandit -r web_app -q --skip B404,B603,B607
ansible-playbook --syntax-check -i my_ansible_inventory_file.ini benchmark-playbook.yml
```

CI (`.github/workflows/ci.yml`) runs these on Python 3.11/3.13/3.14. The bandit skips are deliberate: the scheduler launches `ansible-playbook` from PATH with list argv, no shell.

## Coordination law (three double-execution collisions occurred 2026-08-28)

1. Before claiming ANY bead: `br list --json` — check closed beads first; a closed target means stop.
2. Reserve every file you will touch via Agent Mail (`reason=<bead id>`); a conflict means STOP, not proceed.
3. If another agent's commits landed within ~30 minutes, assume concurrent execution is live and message before editing shared files.
4. `br sync --flush-only` before any commit that includes `.beads/`.

## Honest-credit floor

- Every fix ships with a regression test that fails on the old code.
- No gate-weakening: relax an assertion only when exactness is not the contract (and say why).
- Closes require cited evidence in the reason; "completed" is not "verified".
- `br ready` empty and no verified gap ⇒ stand down. Do not fabricate work; do not file speculative beads without a named consumer and an observed defect class.

## Repo facts that bite

- **Single uvicorn worker only** — each worker spawns its own scheduler and would duplicate benchmark runs.
- **All runtime paths are repo-root anchored** (logs, SQLite, inventory, playbook): launching from any CWD is safe. Keep it that way.
- **`my_ansible_inventory_file.ini` holds IANA placeholder hosts (`1.2.3.4/5/6`) and a placeholder key.** Real multi-machine monitoring is blocked until the operator supplies real hosts. Live verification uses scratch `connection=local` inventories with `HOME` redirected — keep operator state untouched.
- `.env` is untracked; `.env.example` is the template. Values are read at import time — restart to apply.
- The scoring script's output write is atomic (`.tmp` + `os.replace`); keep it that way — `job()` picks the newest `*.json` by mtime.
- Timestamps are local-naive by convention (mtime-derived), documented at both the scheduler and API cutoff sites. Do not convert one side to UTC alone.

## Current state (2026-08-29)

Fully verified: 61 tests, 100% statement coverage, full type annotations, live single-host + two-host pipeline runs, cross-host ranking exact (400/6 vs 200/6), production uvicorn smoke with graceful teardown, CI green on all three Pythons. 24 closed beads in `.beads/`. When in doubt about how something should behave, the beads' close reasons cite the evidence.

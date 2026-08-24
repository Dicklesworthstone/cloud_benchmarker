# Changelog

All notable changes to Cloud Benchmarker are documented in this file.

This project has no formal releases or tags. Changes are organized by date and grouped by capability area. All development occurs on the `main` branch.

Repository: <https://github.com/Dicklesworthstone/cloud_benchmarker>

---

## 2026-08-23 (2) -- Hardening, CI, and Lint

### Scheduler (`web_app/app/utils/scheduler.py`)

- **Eliminated a potential playbook hang**: `run_playbook` piped stdout and stderr separately but drained them sequentially, so a chatty stderr stream could fill its 64 KB pipe buffer while the reader blocked on stdout -- the classic `Popen` deadlock. Stderr is now merged into the stdout stream.
- `parse_inventory` rewritten with a tolerant regex: any key order, extra inline variables, group headers, blank lines, and comments are handled; hosts without an `ansible_host` assignment are skipped (previously it split on spaces and crashed on reordered keys). Covered by two new tests.

### Continuous Integration (`.github/workflows/ci.yml`)

- Added a GitHub Actions workflow: ruff lint, pytest, and Ansible playbook syntax-check across Python 3.11/3.13 on every push to `main` and every PR.

### Lint Policy (`pyproject.toml`)

- Replaced the deprecated top-level ruff setting with an explicit `[tool.ruff.lint]` policy (`E/F/W/I`, `E501` still ignored) so lint results are deterministic regardless of machine-local ruff configuration; import ordering normalized across the codebase via `ruff --fix`.

### Deep Review (import-graph sweep)

- **Anchored Ansible paths to the repository root** (`web_app/app/utils/scheduler.py`): the playbook path was resolved against the process CWD, so launching the server from any other directory (e.g., a systemd unit or `PYTHONPATH`-only deployment) made every benchmark tick fail with `FileNotFoundError` -- silently, forever. The playbook and a relative inventory path from `.env` are now anchored to the repo root via the module location; absolute paths in `.env` are still honored. Regression-tested and verified by booting the app from `/tmp`.
- **Converted blocking endpoints from `async def` to `def`** (`/benchmark_charts/`, `/benchmark_historical_csv/`, and `generate_benchmark_charts`): they contain no `await`s but seconds of pandas/plotly work, which monopolized the event loop and stalled every other request while rendering. Sync endpoints run in FastAPI's threadpool instead.
- Investigated and cleared: SQLAlchemy 2.0's file-SQLite `QueuePool` was suspected of cross-thread connection reuse errors (scheduler thread + request worker threads share one engine); an empirical probe showed the pysqlite dialect already permits cross-thread checkout, so no change was required.

### Input Hardening (fresh-eyes audit, round 2)

- Both combined-results parsers (`parse_combined_results`, `load_combined_results`) previously accepted any valid JSON -- a bare number, list, string, or boolean flowed through and crashed the consumer (`AttributeError` on `.keys()`/`.items()`), which the scheduler would then retry forever on permanently malformed input. Non-object JSON is now rejected as empty with a warning.
- Added seeded fuzz tests pinning two core contracts: the parser returns a dict or raises exactly `JSONDecodeError` for 1,000 arbitrary inputs, and the scorer always maps every input host to a score within 0..100 across 400 randomized workloads (negative values, extreme magnitudes, missing metrics, ties, both weighting modes). Suite now 29 tests.

### Fresh-Eyes Audit Fixes

- **Charts (`web_app/app/chart.py`)**: dropdown visibility used a plain substring test, so selecting IP `10.0.0.1` also revealed traces for `10.0.0.10`, `10.0.0.100`, etc. Trace names are now parsed into exact `(IP, metric)` segments before matching. Covered by dedicated unit tests.
- **Scheduler (`web_app/app/utils/scheduler.py`)**: a non-reentrant lock now guards `run_job_safely` so a long-running playbook can never stack a second concurrent benchmark run -- overlapping ticks are skipped with a warning. The configured interval is clamped to at least one minute (the `schedule` library rejects smaller values). Both covered by tests.
- **Scoring (`script_to_generate_overall_benchmark_scores_from_subscores.py`)**: hosts may report different metric subsets (the playbook skips failed tests per host); the normalizer previously crashed with a `KeyError` on the first host's metrics missing from a later host. It now normalizes each metric across the hosts that reported it and contributes a neutral 50 for unreported metrics. Empty input returns `{}`. Both covered by tests.

### Scheduler Staleness Guard (`web_app/app/utils/scheduler.py`)

- The scheduler no longer attaches a previous run's overall scores to a new run's timestamp: an overall scores file older than the combined results (meaning this run's scoring step failed) is skipped with a warning, and raw subscores are ingested alone. Covered by a dedicated test; `should_run_job` staleness semantics are now unit-tested as well.

### Ansible Compatibility (`benchmark-playbook.yml`) -- found by executing the full pipeline locally

- **Fixed a fatal `set_fact`/`combine` failure on modern ansible-core**: the FileIO result was routed through an intermediate `regex_search` extraction whose lazily-evaluated value `combine` rejects (`expected dicts but got a '_AnsibleLazyTemplateDict'`), aborting the host before any results were saved -- i.e., the playbook silently produced zero results on current ansible releases. The FileIO shell task now emits pure JSON on stdout (prepare/cleanup output silenced) and merges via the same `stdout | from_json` pattern as the other four tests; the extraction and assert tasks were deleted.
- **Replaced undefined `ansible_user` in the save/fetch tasks** with the connection-provided `ansible_user_dir` fact (`ansible_user` is an SSH-inventory variable and is undefined for `ansible_connection=local`).
- Verified end-to-end: the full playbook runs green against local targets -- both single-host (ok=21/failed=0) and three-host fan-out (ok=15 x3) -- producing real sysbench results that the scheduler ingests and serves through every endpoint, including correct per-host score pairing in the CSV export.

### Screenshots

- Regenerated both README screenshots from the live application: the API docs now show Swagger at `/docs` (including the new `TimePeriod` enum schema), and the charts capture shows the dashboard's per-IP dropdown subscore chart and overall score chart with current Plotly rendering. Captures use synthetic seed data (three hosts over six runs).

## 2026-08-23 -- Correctness and Robustness Overhaul

### Scoring Engine (`script_to_generate_overall_benchmark_scores_from_subscores.py`)

- **Fixed inverted latency normalization**: `mutex_test__avg_latency` and `threads_test__avg_latency` were normalized as higher-is-better, so the machine with the *worst* latency earned 100 and the machine with the *best* latency earned 0 -- actively rewarding worse hardware. Normalization is now direction-aware: 100 always means best-in-class.
- A strictly superior host now scores exactly 100 under equal weighting (previously ~60 due to the latency inversion).
- Single-host results and metrics where all hosts tie now contribute a neutral 50 instead of a fake perfect 100.
- The input parser accepts strict JSON (the playbook's current output) in addition to the legacy quasi-JSON format with unquoted host keys.
- Input/output paths derive from `os.path.expanduser("~")` instead of a hardcoded `/home/ubuntu`, aligning with the scheduler's paths for any control-node user.

### Scheduler (`web_app/app/utils/scheduler.py`)

- **Fixed permanent silent scheduler death**: previously the first `job()` ran inline before the polling loop started, so one exception (e.g., a failed playbook run producing an empty/garbage combined file, which the old parser crashed on via `"{}"` -> `"{{}}"`) killed the daemon thread and benchmarking never ran again while the web app kept serving stale data. The polling thread now starts first and every tick runs through `run_job_safely()`, which logs failures and retries on the next scheduled interval.
- Replaced `os.getlogin()` (raises without a controlling terminal) with `getpass.getuser()`.
- Combined-results loading tolerates empty files, strict JSON, and legacy quasi-JSON; empty data skips ingestion cleanly with a warning.
- Playbook stderr is captured and logged; playbook subprocess sessions are closed deterministically.
- Database sessions are closed after ingestion.

### API (`web_app/app/routes/api_routes.py`)

- `time_period` is validated as an enum query parameter: unsupported values now return a proper 422 instead of a 500 `ResponseValidationError`.
- **Fixed cross-host score misattribution in the historical CSV**: `pd.merge_asof` joined on timestamp alone, arbitrarily attaching one host's overall score to another host's raw rows. The merge now keys on `hostname` as well.
- Empty-database CSV requests return a header-only file instead of crashing.

### Charts (`web_app/app/chart.py`)

- Empty databases receive a friendly "no data yet" page instead of a `KeyError` 500.
- The Plotly JavaScript bundle is inlined once per page instead of once per figure, halving the charts response size (~9.7 MB -> ~4.9 MB). Subscore figure construction extracted into `build_subscore_figure`.

### Dashboard Wiring (`web_app/app/main.py`, `web_app/static/index.html`)

- The root URL now serves the actual dashboard page; interactive API docs moved to `/docs`; `web_app/static/` is mounted at `/static`.
- Fixed the dashboard iframe, which pointed at `/chart/` -- a route that has never existed; startup migrated to the non-deprecated FastAPI lifespan handler.

### Ansible Playbook (`benchmark-playbook.yml`)

- The combine stage no longer produces invalid pseudo-JSON (`hostA: {...},` with unquoted keys): it emits strict JSON via shell assembly, skips hosts whose result files are missing or empty, and writes to the invoking user's home directory.

### Dependencies and Tooling

- `requirements.txt` now pins major-version caps (previously fully unpinned); the deprecated standalone `plotly-express` shim was removed (use `plotly`'s built-in `plotly.express`).
- Added `requirements-dev.txt` and a 16-test pytest suite covering scoring math direction, parser format tolerance, ingest upsert semantics, endpoint contracts (including regressions for each bug above), and empty-state behavior.
- `data_models.py` modernized to SQLAlchemy 2.x `declarative_base` location and Pydantic v2 `ConfigDict`.

## 2026-02-21 / 2026-02-22 -- Licensing, Branding, and Code Quality

### Licensing

- Created `LICENSE` file containing the MIT License with OpenAI/Anthropic Rider, which restricts use by OpenAI, Anthropic, and their affiliates without express written permission from Jeffrey Emanuel.
  [`a8ee365`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/a8ee36557f9a6a9388acd79027cb1ead3f27a908)
- Updated the README license section to reference "MIT License (with OpenAI/Anthropic Rider)" instead of plain "MIT License".
  [`0c1e58c`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/0c1e58c9c93b046c408362ea4c59a14dde583607)

### Social Preview

- Added GitHub Open Graph social preview image (`gh_og_share_image.png`, 1280x640) so the repository displays a branded card when shared on social media and chat platforms.
  [`1d0f4a2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1d0f4a231d5d2df8286bfe880f082f198e0b4fd0)

---

## 2026-02-11 -- Linting Fix

### Code Quality

- Removed a spurious `f"..."` prefix from the `/benchmark_charts/` endpoint description string in `api_routes.py`. The string contained no interpolated variables, so the f-prefix triggered pylint W1309 / ruff F541 warnings for no benefit.
  [`6153af7`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/6153af741f2b6013c705d9df06b42f269c39f6f2)

---

## 2023-09-28 -- Initial Development

Every commit in this section landed on the same day the project was created. They are grouped below by capability rather than chronological order.

### Benchmarking Engine

The core of Cloud Benchmarker: an Ansible playbook that orchestrates five sysbench tests across remote hosts and a Python scoring script that normalizes and ranks results.

- **Ansible playbook** (`benchmark-playbook.yml`): installs `sysbench`, `gawk`, and `grep` on targets, then runs five tests per host -- CPU (events/sec, 4 threads), memory (MiB transferred, 1K blocks over 100G), file I/O (random read/write reads/sec), mutex (avg latency, 10K locks, 128 mutexes), and threads (avg latency, 4 threads). Results are saved to per-host JSON files, fetched to the control node, and assembled into a single combined JSON.
- **Scoring script** (`script_to_generate_overall_benchmark_scores_from_subscores.py`): normalizes each metric to 0--100, applies configurable custom weights (e.g., CPU and memory at 2x, mutex and threads at 0.5x), sums them into an overall score, and writes sorted results to a JSON file.

Introduced in:
[`1fc4111`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1fc411169a7bbbed17a48a93315c3e549dbdeacb)

### Web Application and API

A FastAPI application serving as both the dashboard and the data-access layer.

- **`GET /data/raw/`** -- returns raw benchmark subscores, optionally filtered by `time_period` (`last_7_days`, `last_30_days`, `last_year`).
- **`GET /data/overall/`** -- returns overall normalized scores with the same time-period filters.
- **`GET /benchmark_charts/`** -- generates interactive Plotly charts with per-metric and per-IP-address dropdown toggles.
- **`GET /benchmark_historical_csv/`** -- merges raw and overall data by closest timestamp and streams a CSV download.
- **Swagger UI** served at `/` as the default docs page (`docs_url="/"`).
- Application version declared as `1.0.0` in the FastAPI constructor.

Introduced in:
[`1fc4111`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1fc411169a7bbbed17a48a93315c3e549dbdeacb)

All four endpoints were later enriched with detailed Swagger metadata -- `summary`, `description` (including parameter documentation and usage examples), and `response_description`:
[`6c26ca2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/6c26ca28f0986fd4469e1fd6857d5d4eaa69a02f)

### Data Layer

- SQLAlchemy ORM models for `RawBenchmarkSubscores` and `OverallNormalizedScore`, backed by SQLite (`cloud_benchmarker.sqlite`).
- Database initialization on application startup via `init_db.py`.

Introduced in:
[`1fc4111`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1fc411169a7bbbed17a48a93315c3e549dbdeacb)

### Scheduler and Automation

A background daemon thread that runs the Ansible playbook on a configurable interval and ingests results into the database automatically.

- Playbook execution interval controlled by `PLAYBOOK_RUN_INTERVAL_IN_MINUTES` (default: 360 min / 6 hours).
- Staleness check: skips playbook execution if output files are less than 3 hours old.
- After each playbook run, results are parsed and ingested into SQLite.

Introduced in:
[`1fc4111`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1fc411169a7bbbed17a48a93315c3e549dbdeacb)

**Hardening (same day):** Several critical fixes were applied to make the scheduler robust for first-run and edge-case scenarios:
[`e6b7428`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/e6b7428f8fdac7e33837e32f13892b84c4c8bf05)

- **Auto-create output directories**: the scheduler now creates `~/benchmark_result_output_files/` and initializes `~/combined_cloud_benchmarker_results.json` with `{}` on first run, instead of crashing when they are absent.
- **Initial-setup flag**: forces the Ansible playbook to execute on first launch regardless of the file-staleness check, then resets the flag so subsequent runs respect the interval.
- **Empty-glob guard**: the `glob.glob()` result is checked for emptiness before calling `max()`, preventing a `ValueError` when no JSON output files exist yet.
- **Computed output paths**: moved `NORMALIZED_BENCHMARK_OUTPUT_FILES_PATH` and `COMBINED_BENCHMARK_SUBSCORE_RESULTS_FILE_PATH` from `.env` variables to paths computed dynamically under the current OS user's home directory (via `os.getlogin()`), simplifying deployment and eliminating a hard dependency on the `/home/ubuntu` username.

### Ansible Inventory and SSH Configuration

- Created example Ansible inventory file (`my_ansible_inventory_file.ini`) with placeholder host IPs and SSH key reference.
  [`fb05b72`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/fb05b72fcb33dfce110afa440ddb932f0cca7268)
- Changed `ansible_private_key_file` from an absolute path (`/home/ubuntu/my-secret-ssh-key.pem`) to a relative path (`my-secret-ssh-key.pem`) for portability across environments.
  [`0c0233f`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/0c0233f442603e6d748f3b68e2ea8f2ac52e2cb5)
- Added placeholder SSH key file (`my-secret-ssh-key.pem`) so the project structure is complete out of the box.
  [`a97ac32`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/a97ac32f0d928b6965e2ea5f63d6c69de25b16db)

### Application Branding

- Simplified the FastAPI title/description by removing extra rocket emoji from both the README heading and the `main.py` description string.
  [`86ea9ad`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/86ea9ad55ca430fed1d5ec6aacc8a1d4e7d6a43d),
  [`d8aba91`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/d8aba91fbbaa05bcde4ccb7ab71cfacc1323bd6b)

### Screenshots and Visual Assets

- Included project logo (`cloud_benchmarker_logo.webp`) and initial Swagger screenshot (`cloud_benchmarker_screenshot.png`) with the first commit.
  [`1fc4111`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1fc411169a7bbbed17a48a93315c3e549dbdeacb)
- Updated the Swagger screenshot to a cleaner capture.
  [`0b5a242`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/0b5a2421ed9bcc7aa4363c65d329dec8d0ca132f)
- Added a charts screenshot (`cloud_benchmarker_screenshot_charts.webp`) showing the Plotly visualization output.
  [`17d6015`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/17d6015eff04da59755494485868e176a5b10050)

### Documentation (README)

The README was iteratively expanded throughout launch day:

- Added installation instructions (venv setup, pip requirements) and initial feature list.
  [`6252fd2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/6252fd2c28069df03c1aa84a984cfb996e9857f6),
  [`422f333`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/422f3333c649e83401e96b93e9b919f8ed6715da)
- Added `source venv/bin/activate` reminder after `pip install` in the install block.
  [`37dd4a2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/37dd4a2c14433a80a58e1e72c43ba91822f73576)
- Updated license line from "See LICENSE.md for details" to "This project is under the MIT License" (no LICENSE file existed yet at that time).
  [`19dac0c`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/19dac0c7d75cb7bf73d84a11196900d571bb3106)
- Separated uvicorn launch command from the install block into its own section; added guidance on editing the Ansible inventory file and documented first-run auto-setup behavior.
  [`012c89d`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/012c89df5a50c619f12d4e6d6b895b86234af424)
- Trimmed the configuration section, removing the "restart required" note since `.env` variables are read at startup by design.
  [`5235b9a`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/5235b9aef7046d16db40b9dcf6365a897e0829b5)
- Split the single screenshot embed into separate "Swagger" and "Charts" sections with distinct headers.
  [`4f9a8d6`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/4f9a8d61d551fed3f522db09ddc4394b832a5e2a)

### Configuration and Tooling

Introduced in the first commit:
[`1fc4111`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1fc411169a7bbbed17a48a93315c3e549dbdeacb)

- `.env` with all runtime settings: database connection string, playbook interval, chart data-point limit, and inventory file path.
- `.gitignore` (Python-standard, 166 lines).
- `.vscode/launch.json` for local debugging.
- `pyproject.toml` (minimal project metadata).
- `requirements.txt`: sqlalchemy, fastapi, schedule, ansible, uvicorn, plotly-express, pandas, pydantic, python-decouple.

---

## Commit Index

| Date | Hash | Summary |
|------|------|---------|
| 2026-02-22 | [`0c1e58c`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/0c1e58c9c93b046c408362ea4c59a14dde583607) | docs: update README license references to MIT + OpenAI/Anthropic Rider |
| 2026-02-22 | [`a8ee365`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/a8ee36557f9a6a9388acd79027cb1ead3f27a908) | chore: update license to MIT with OpenAI/Anthropic Rider |
| 2026-02-21 | [`1d0f4a2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1d0f4a231d5d2df8286bfe880f082f198e0b4fd0) | chore: add GitHub social preview image (1280x640) |
| 2026-02-11 | [`6153af7`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/6153af741f2b6013c705d9df06b42f269c39f6f2) | Remove unnecessary f-string prefix from static description string |
| 2023-09-28 | [`4f9a8d6`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/4f9a8d61d551fed3f522db09ddc4394b832a5e2a) | Update README.md (Swagger/Charts section split) |
| 2023-09-28 | [`17d6015`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/17d6015eff04da59755494485868e176a5b10050) | Add charts screenshot |
| 2023-09-28 | [`5235b9a`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/5235b9aef7046d16db40b9dcf6365a897e0829b5) | Update README.md (trim config section) |
| 2023-09-28 | [`0b5a242`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/0b5a2421ed9bcc7aa4363c65d329dec8d0ca132f) | Update Swagger screenshot |
| 2023-09-28 | [`012c89d`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/012c89df5a50c619f12d4e6d6b895b86234af424) | Update README.md (inventory + first-run docs) |
| 2023-09-28 | [`d8aba91`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/d8aba91fbbaa05bcde4ccb7ab71cfacc1323bd6b) | Update main.py (simplify description emoji) |
| 2023-09-28 | [`86ea9ad`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/86ea9ad55ca430fed1d5ec6aacc8a1d4e7d6a43d) | Update README.md (simplify heading emoji) |
| 2023-09-28 | [`19dac0c`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/19dac0c7d75cb7bf73d84a11196900d571bb3106) | Update README.md (license line) |
| 2023-09-28 | [`a97ac32`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/a97ac32f0d928b6965e2ea5f63d6c69de25b16db) | Add placeholder SSH key file |
| 2023-09-28 | [`0c0233f`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/0c0233f442603e6d748f3b68e2ea8f2ac52e2cb5) | Update my_ansible_inventory_file.ini (relative key path) |
| 2023-09-28 | [`fb05b72`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/fb05b72fcb33dfce110afa440ddb932f0cca7268) | Create my_ansible_inventory_file.ini |
| 2023-09-28 | [`37dd4a2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/37dd4a2c14433a80a58e1e72c43ba91822f73576) | Update README.md (venv activate reminder) |
| 2023-09-28 | [`6c26ca2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/6c26ca28f0986fd4469e1fd6857d5d4eaa69a02f) | Update swagger docs (endpoint metadata) |
| 2023-09-28 | [`e6b7428`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/e6b7428f8fdac7e33837e32f13892b84c4c8bf05) | Bug fixes (scheduler hardening) |
| 2023-09-28 | [`422f333`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/422f3333c649e83401e96b93e9b919f8ed6715da) | readme (install instructions + config docs) |
| 2023-09-28 | [`6252fd2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/6252fd2c28069df03c1aa84a984cfb996e9857f6) | readme (minor formatting) |
| 2023-09-28 | [`1fc4111`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/1fc411169a7bbbed17a48a93315c3e549dbdeacb) | first commit (full application) |

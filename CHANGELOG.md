# Changelog

All notable changes to Cloud Benchmarker are documented in this file.

This project has no formal releases or tags. Changes are organized by date and grouped by capability area. All development occurs on the `main` branch.

Repository: <https://github.com/Dicklesworthstone/cloud_benchmarker>

---

## 2026-08-28 -- Path Independence, Ingest Isolation, and Type Coverage

### Runtime Paths Are Now Fully CWD-Independent (`logger_config.py`, `database/init_db.py`, `utils/scheduler.py`)

- **Logs no longer depend on the launch directory**: `cloud_benchmarker.log` and `old_logs/` were resolved against the process CWD, so a systemd launch from `/` scattered logs there. Both are now anchored to the repository root (`LOG_FILE_PATH` / `OLD_LOGS_DIR`), extending the earlier playbook-anchoring doctrine. The rotation namer/rotator tests were reworked to monkeypatch the anchored directory instead of relying on CWD.
- **Relative SQLite URLs are anchored**: the default `sqlite:///cloud_benchmarker.sqlite` resolved against the CWD, silently creating a second empty database when launched elsewhere. `init_db` now rewrites relative `sqlite:///` URLs (plain and dialect-prefixed) against the repository root, preserving query suffixes and leaving absolute paths, `:memory:`, `file:` URIs, and non-SQLite dialects untouched. Every variant is unit-tested.
- **Inventory parsing follows the playbook's anchoring**: `job()` handed the raw -- possibly relative -- decouple value to `parse_inventory` while `run_playbook` used the anchored absolute path, so a non-repo launch made benchmark runs succeed while inventory parsing failed with `FileNotFoundError` and retried forever. Regression-tested from a foreign CWD.

### Ingest Isolation (`utils/scheduler.py`)

- **One malformed host can no longer lose the batch**: unexpected keys in a host's raw subscores (playbook JSON drift) raised inside `ingest_data` and aborted ingestion for every host; the combined results file is overwritten by the next run, so the data was gone. Each host is now validated before mutation: a non-object payload or unknown keys skip that host only (logged with the offending keys), and a non-numeric overall score drops just that host's overall row while its raw subscores still land. The single per-run commit is preserved; genuinely environmental database errors still fail the batch loudly.

### Time Handling (`utils/scheduler.py`, `routes/api_routes.py`)

- **Staleness math uses absolute epoch seconds**: `should_run_job` subtracted naive local datetimes, so a DST transition between a file's mtime and the check could distort measured staleness by up to an hour. The comparison is now `time.time() - getmtime` against a threshold in seconds.
- **The local-naive timestamp convention is documented at both ends**: stored run timestamps are the results file's local mtime, and API period cutoffs intentionally match. A UTC migration would require converting every existing row (SQLite DateTime columns drop tzinfo on round-trip) and is deliberately not attempted one-sided.

### Type Coverage (`utils/scheduler.py`, `routes/api_routes.py`, `chart.py`, scoring script)

- All previously untyped public functions are annotated -- the eight scheduler functions, the four API route handlers (typed to their response-model contracts via `cast` over the ORM rows FastAPI serializes), both chart builders, and both scoring-script entry points -- with no loosening of the mypy configuration. `run_playbook` now raises a loud `RuntimeError` on the impossible `stdout=None` case instead of silently skipping the output drain.

### Startup and Subprocess Test Coverage (`tests/test_app_boot.py`)

- Three new tests boot the REAL application: the lifespan wiring through `TestClient` (init_db runs, the dashboard route serves the static page, the scheduler daemon thread starts), `run_playbook` against an executable stub on PATH (pins the repo-anchored argv contract, streams >64 KB on both pipes to prove the merged-output drain cannot deadlock, runs from a foreign CWD), and `start_scheduler`'s registration (guarded wrapper, interval clamp, one polling thread, inline first tick). Coverage: `main.py` 0% -> 100%, `scheduler.py` 87% -> 95%, project total 98%. Suite now 49 tests.

### Documentation and Configuration

- README's scheduler section no longer claims a hardcoded "older than 3 hours" check; it documents the half-interval threshold. The Configuration section now lists every `.env` knob in a table (`SQLALCHEMY_ENGINE_CONNECTION_STRING`, `PLAYBOOK_RUN_INTERVAL_IN_MINUTES`, `ANSIBLE_INVENTORY_FILE_PATH`, `MAX_DATA_POINTS_FOR_CHART`), and a [`.env.example`](.env.example) template ships with the repository.

### Tracker and Live Verification

- Work was tracked in a newly initialized beads tracker (`.beads/`) as `cloud_benchmarker-pz9`, `-m8o`, `-3v2`, `-m2i`, `-f3k`, and `-uu7`, all closed with cited evidence.
- **The full pipeline was re-verified end-to-end against localhost** (scratch inventory, `HOME` redirected to a scratch directory so operator state was untouched): the real playbook ran all five sysbench tests, the combine stage and scoring script produced valid artifacts, the real `scheduler.job()` ingested them into a throwaway database through the unmodified default wiring, and all four API endpoints served the fresh data. The run immediately paid for itself: it exposed the neutral-50 float dust fixed above (the unit suite's `pytest.approx` assertions had been masking it), which is now proven exact (`50.0`) in regenerated playbook output.
- **The multi-host mechanics were verified live with a two-host run** (two `connection=local` inventory entries, scratch `HOME`): the combine stage joined both per-host fetch directories, scoring emitted two exact neutral-`50.0` entries (the float-dust fix holds for N hosts), one `job()` tick landed two raw rows sharing a run timestamp plus two overall rows, the historical CSV paired each host with its own score, and the charts rendered multi-host traces.
- **The canonical production launch was smoke-tested under real uvicorn** (scratch-`HOME` artifacts, loopback bind): all four endpoints plus `/docs` and the dashboard served correctly over real HTTP — including the documented 422 validation on a bogus `time_period` — the uvicorn-spawned scheduler thread ran its real first tick inside the server (staleness guard skipped the playbook on fresh data and still ingested), and a graceful SIGTERM produced the full ordered teardown with `engine.dispose()` and zero errors.

---

## 2026-08-23 (2) -- Hardening, CI, and Lint

### Scheduler (`web_app/app/utils/scheduler.py`)

- **Eliminated a potential playbook hang**: `run_playbook` piped stdout and stderr separately but drained them sequentially, so a chatty stderr stream could fill its 64 KB pipe buffer while the reader blocked on stdout -- the classic `Popen` deadlock. Stderr is now merged into the stdout stream.
- `parse_inventory` rewritten with a tolerant regex: any key order, extra inline variables, group headers, blank lines, and comments are handled; hosts without an `ansible_host` assignment are skipped (previously it split on spaces and crashed on reordered keys). Covered by two new tests.

### Continuous Integration (`.github/workflows/ci.yml`)

- Added a GitHub Actions workflow: ruff lint, pytest, and Ansible playbook syntax-check across Python 3.11/3.13 on every push to `main` and every PR.

### Lint Policy (`pyproject.toml`)

- Replaced the deprecated top-level ruff setting with an explicit `[tool.ruff.lint]` policy (`E/F/W/I`, `E501` still ignored) so lint results are deterministic regardless of machine-local ruff configuration; import ordering normalized across the codebase via `ruff --fix`.

### Scheduler Intervals Now Behave As Documented

- **Fixed the hardcoded 3-hour staleness threshold silently overriding shorter intervals**: `should_run_job` compared file age against a fixed `timedelta(hours=3)`, so `PLAYBOOK_RUN_INTERVAL_IN_MINUTES=60` still only benchmarked every ~3 hours, contradicting the README's "set the schedule to any interval". The threshold is now half the configured interval -- the 360-minute default yields the historical 3 hours (behavior unchanged out of the box), while shorter intervals genuinely take effect. Both behaviors are regression-tested (suite now 41 tests).

### Static Analysis Adoption (mypy + bandit)

- **Migrated `data_models.py` to SQLAlchemy 2.0's `class Base(DeclarativeBase)` style** -- the recommended modern form, and it makes the models fully transparent to static type checkers (`mypy` now passes clean on all 8 source files under mypy 2.3).
- Configured `[tool.mypy]` in `pyproject.toml` (explicit package bases for the namespace-package layout; per-module import overrides for `decouple`/`pandas`/`plotly`, which ship no stubs).
- **Added a static-analysis step to CI**: `mypy` over the app and scoring script, plus `bandit` over `web_app` (skipping B404/B603/B607 -- the scheduler deliberately launches `ansible-playbook` from PATH with list argv and no shell). Both pass clean; `mypy` and `bandit` added to `requirements-dev.txt`.

### Coverage-Driven Consistency Fix

- **Fixed inconsistent degradation in the scheduler**: when raw results existed but no overall scores file had ever been produced, `job()` skipped ingestion entirely -- permanently losing that run's raw subscores, since the next run overwrites the combined results file. Raw data now always ingests when present; overall scores merge best-effort (fresh file, stale file, or none), matching the stale-file path's behavior. Regression-tested; suite grown to 36 tests with new coverage of the chart composition paths (raw-only / overall-only), the overall endpoint's time filter, and the first-boot playbook branch.
- Added `pytest-cov` to `requirements-dev.txt`; current branch coverage: `chart.py`/`api_routes.py`/`data_models.py` 100%, `scheduler.py` 87% (remainder is the subprocess/thread-loop body verified by live end-to-end runs).

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
| 2026-08-28 | [`3b2fe4b`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/3b2fe4b7404f9edf0e1386a1f030f632ab221b44) | test(coverage): close the last gaps -- 100% statement coverage |
| 2026-08-28 | [`796fa97`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/796fa9773879ea1af7a8d5b06ac4b7adbbf9df24) | chore(config): stop tracking .env; document cp from .env.example |
| 2026-08-28 | [`448cbaa`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/448cbaa972c459f360ce6238097e4060b5d28798) | docs(changelog): record the localhost end-to-end pipeline verification |
| 2026-08-28 | [`d023ebd`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/d023ebdba58e4cbf69596c8876b7a23f1103d343) | fix(scoring): round away weight-normalization dust so contract values are exact |
| 2026-08-28 | [`6f8ed80`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/6f8ed80837c16dd7a30974589eaa31c8dbb36dba) | chore(beads): file E2E localhost pipeline verification (evc) |
| 2026-08-28 | [`7560acf`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/7560acf528e2ee26506741e3fb9ea7fdf6c6f718) | fix(app): dispose the SQLAlchemy engine on lifespan shutdown |
| 2026-08-28 | [`de0cb3c`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/de0cb3c74b35106a4e22d8b0a6c6b87cb1ee25af) | test(scoring): exercise the __main__ CLI path; clamp scores to [0, 100] |
| 2026-08-28 | [`3627f47`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/3627f47440945be25919f700a778382534272c6e) | fix(scoring): clamp overall scores to [0, 100] after weighting |
| 2026-08-28 | [`c5140b8`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/c5140b8918fa945a4be4bb25412739d76f66a0ed) | test(scoring): drive the __main__ CLI via a real subprocess |
| 2026-08-28 | [`96bb6e3`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/96bb6e3e138c5b896a111121c3eea0770c8a8e67) | chore(beads): mark cloud_benchmarker-vgn in progress |
| 2026-08-28 | [`85d6a5e`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/85d6a5e209394bfd92474c85bb97c89eae99e2af) | fix(scheduler): judge overall-file freshness by mtime, not ctime |
| 2026-08-28 | [`7fe409c`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/7fe409c853a85c25fce9e6802df3252f9555d596) | fix(scheduler): compare overall-file freshness with mtime, not ctime |
| 2026-08-28 | [`2a655e3`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/2a655e33d94811ef7dcb8c1b256ef8f8dac0f8b7) | chore(beads): file three follow-ups from the hardening round |
| 2026-08-27 | [`5efeddb`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/5efeddb605222923773be359f603a947f9fc35d8) | docs: refresh README configuration truth, add .env.example, changelog the hardening round |
| 2026-08-27 | [`26744f9`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/26744f947a6deabbb00b5f227875eb36fe10580d) | refactor(types): annotate all public functions across web_app and the scoring script |
| 2026-08-27 | [`8b886ac`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/8b886ac062eef41e317a0df46c46b4c199f1bb7f) | test(app): boot the real lifespan, pin the playbook argv, cover the scheduler registration |
| 2026-08-27 | [`ea2f25f`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/ea2f25f1388b8b1d073510798bc868d0b7841b88) | fix(scheduler): epoch-based staleness math; document the local-naive time convention |
| 2026-08-27 | [`7b4309f`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/7b4309f9abffe39e1166f9d25cc488f9bc4a8770) | fix(scheduler): measure staleness in epoch seconds across DST |
| 2026-08-27 | [`6492517`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/6492517cda5c83db3f559f8cd258d1f5915add3d) | fix(scheduler): isolate malformed hosts so one bad row cannot lose the batch |
| 2026-08-27 | [`f77d824`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/f77d8240b0dbf8a720fafb15ed8c63c9d85f0d45) | fix(ingest): isolate malformed hosts so one bad row cannot lose the batch |
| 2026-08-27 | [`560a3be`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/560a3be40dcea6d552358ab1cbf1333d98a01ea6) | chore(beads): mark cloud_benchmarker-m8o in progress |
| 2026-08-27 | [`e3ccee2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/e3ccee2900d5210d3f2f0baa0bbbec5b7fbf6223) | fix(paths): anchor logs, SQLite, and inventory parsing to the repo root |
| 2026-08-26 | [`8689ac5`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/8689ac54a7d7a993b5d5934018f7018e50e11c64) | refactor: single source of truth for metric columns in data_models |
| 2026-08-26 | [`39fe43b`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/39fe43bc3aaf42990bcb4f793577bcd26b07a050) | refactor(metrics): import canonical METRIC_COLUMNS in chart and api_routes |
| 2026-08-26 | [`4f51a67`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/4f51a67813764f450be8011f260fa38169732b9a) | feat(scoring): import METRIC_COLUMNS from data models in scoring script |
| 2026-08-26 | [`0a44d02`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/0a44d025991fb58617a0d61b11e67d1546fc15ee) | docs(models): document METRIC_COLUMNS external schema contract |
| 2026-08-26 | [`4e48ba9`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/4e48ba94ead03522323371d7545459d0cccc1d1d) | docs: changelog entry for interval-derived staleness threshold |
| 2026-08-26 | [`73a4f4e`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/73a4f4e3a5a02cd570d621d123359e403e6f6d77) | fix(scheduler): derive staleness threshold from configured interval |
| 2026-08-25 | [`a396daf`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/a396daf59fb8e5a7ebfb271bd1f626c25c1ab281) | test: cover logger rotation namer/rotator and init_db; document code quality commands |
| 2026-08-25 | [`ccea28c`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/ccea28c320458f7e108da34b929174b291081f45) | feat(ci): adopt mypy and bandit static analysis; modernize Base to DeclarativeBase |
| 2026-08-25 | [`22b286c`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/22b286cfa0a6c7d7150f9be92129af00f9ccaf8d) | ci: bump actions/checkout to v5 and actions/setup-python to v6 |
| 2026-08-25 | [`ef16f32`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/ef16f32ed90c1eb729920e26cad1cb3fe937ed8f) | feat(ansible,tests): configure accept-new SSH host checking and pytest session engine disposal |
| 2026-08-25 | [`cb3419f`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/cb3419fdfea2ea38a3ae00721e922bbb90064a1d) | fix(scheduler): ingest raw subscores even when no overall file exists; close coverage gaps |
| 2026-08-24 | [`fd508d0`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/fd508d01864e3c707031c1375a8ba5ffc2060e88) | docs: document single uvicorn worker constraint for scheduler isolation |
| 2026-08-24 | [`8f58d18`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/8f58d18845992358a37a6ddb92897d458fd58aaf) | fix: anchor Ansible paths to repo root; run blocking endpoints in threadpool |
| 2026-08-24 | [`4d0dcf5`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/4d0dcf528082842b2b7488433b0182629e755a57) | feat(scheduler,api): anchor ansible paths to repo root and offload sync pandas chart generation |
| 2026-08-24 | [`b33402a`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/b33402ac8e71a854b538d3fee1da7bdd3db4a2fe) | fix: reject non-object JSON in results parsers; pin parser/scorer contracts with seeded fuzz tests |
| 2026-08-24 | [`47d44a2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/47d44a2f4b6fba7397812c85368f4176c538cad8) | fix: fresh-eyes audit -- exact chart dropdown matching, scheduler tick lock, tolerant scoring |
| 2026-08-24 | [`d5a8964`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/d5a8964f086625fd9bf01039947ffb0f86cb6983) | test: cover should_run_job staleness semantics; docs: restore changelog subsections lost to working-tree reset |
| 2026-08-24 | [`d292398`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/d2923981f7becdb5cc13c18f869478c98f89cba1) | test(scheduler): add test for should_run_job staleness semantics |
| 2026-08-24 | [`96a4a39`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/96a4a390aad7bdfc6da775bdfb3a55393d793f9b) | fix(playbook): re-land FileIO modernization and ansible_user_dir dests wiped by working-tree reset |
| 2026-08-24 | [`bb87a8a`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/bb87a8ac07c7f918067ce2195ccfa35ad381136f) | fix(api): preserve IP_address in degenerate CSV branch; restore regression test lost to working-tree reset |
| 2026-08-24 | [`73c6b79`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/73c6b794403516b07d9cbafd810e7814f1309d65) | test(api,scheduler): update test suites for scheduler and api |
| 2026-08-24 | [`9a2171f`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/9a2171f6f6d2bc177fd401d0c2f11ba3d755c20b) | fix(scheduler): reject stale overall scores predating combined benchmark results |
| 2026-08-23 | [`d8bb04a`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/d8bb04a916e898a2d543e0b7f6ecdbbd98f47005) | docs: document hardening, CI, and test suite; make VS Code launch portable |
| 2026-08-23 | [`637c1bf`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/637c1bfa8c007bc53914b2a706bcd9db809c3600) | test: add 16-test pytest suite, GitHub Actions CI, and dev requirements |
| 2026-08-23 | [`f2cb5e8`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/f2cb5e82d4ba700dc80e86305694cb261f367344) | fix(scheduler): eliminate playbook pipe deadlock; make parse_inventory tolerant |
| 2026-08-23 | [`a0d16a2`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/a0d16a248700475582734e046ece7a8c5595662c) | refactor(web_app): normalize imports under explicit ruff policy; modernize to SQLAlchemy 2.x / Pydantic v2 |
| 2026-08-23 | [`4f7de8a`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/4f7de8adf24b7329e6ce05f6410157e823b83699) | feat(web_app): lifespan-based app shell, static dashboard at /, typed filters, no-data charts |
| 2026-08-23 | [`07a811a`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/07a811ae2c8129618cb21805800b94f9fe17f3af) | fix(benchmark): strict-JSON combined results; portable home paths; tolerant results loader |
| 2026-03-21 | [`e2f08fb`](https://github.com/Dicklesworthstone/cloud_benchmarker/commit/e2f08fb94a764dd1902b8a2274edcc885f931ab5) | docs: add comprehensive CHANGELOG.md documenting project history |
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

"""Tests for the real application surface: lifespan wiring, the dashboard
route, the playbook subprocess, and the scheduler's polling registration.

These cover the previously untested startup and execution paths (main.py was
at 0% coverage; run_playbook and start_scheduler's loop body were skipped by
the coverage report).
"""
import os


def test_real_app_lifespan_boots_dashboard_and_shuts_down(monkeypatch):
    # Boot the REAL web_app.app.main.app through TestClient so the lifespan
    # wiring (init_db + scheduler daemon thread) is exercised, not bypassed.
    import time as time_module

    from fastapi.testclient import TestClient

    from web_app.app import main as main_module

    boot = {"init": 0, "scheduler": 0}
    monkeypatch.setattr(main_module, "init_db", lambda: boot.__setitem__("init", boot["init"] + 1))

    def fake_start_scheduler():
        boot["scheduler"] += 1

    monkeypatch.setattr(main_module, "start_scheduler", fake_start_scheduler)

    with TestClient(main_module.app) as client:
        assert boot["init"] == 1  # lifespan initialized the database
        response = client.get("/")  # dashboard route serves the static page
        assert response.status_code == 200
        assert "Cloud Benchmarker Dashboard" in response.text

    for _ in range(50):  # the scheduler thread is asynchronous; wait briefly
        if boot["scheduler"] == 1:
            break
        time_module.sleep(0.02)
    assert boot["scheduler"] == 1


def test_run_playbook_drains_merged_output_without_deadlock(tmp_path, monkeypatch):
    # A chatty playbook (>64 KB on both stdout and stderr) must be drained
    # line-by-line with stderr merged into stdout, or a full stderr pipe
    # buffer blocks the scheduler thread forever. The stub also pins the
    # exact argv contract: repo-anchored, absolute playbook and inventory.
    from web_app.app.utils import scheduler

    marker = tmp_path / "stub_completed"
    stub = tmp_path / "ansible-playbook"
    # Assert against the module's own anchored constants so the contract
    # (absolute, repo-anchored paths handed to the playbook binary) is
    # verified regardless of which inventory name the test env configures.
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[0] == '-v' and args[1] == '-i', args\n"
        f"assert os.path.isabs(args[2]) and args[2] == {str(scheduler.ANSIBLE_INVENTORY_ABSOLUTE_PATH)!r}, args\n"
        f"assert os.path.isabs(args[3]) and args[3] == {str(scheduler.PLAYBOOK_FILE_PATH)!r}, args\n"
        "for i in range(2000):\n"
        "    print(f'stdout line {i}')\n"
        "    print(f'stderr line {i}', file=sys.stderr)\n"
        f"open({str(marker)!r}, 'w').write('done')\n"
        "sys.exit(0)\n"
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.chdir(tmp_path)  # argv must stay repo-anchored regardless of CWD

    scheduler.run_playbook()

    assert marker.exists()  # stub reached its end without argv or write errors


def test_start_scheduler_registers_guarded_job_and_runs_first_tick(monkeypatch):
    # start_scheduler must register the GUARDED wrapper (never bare job()),
    # clamp the interval to the schedule library's one-minute minimum, spawn
    # the polling thread, and run the first tick inline.
    from web_app.app.utils import scheduler

    registered = {}
    thread_targets = []
    ticks = []

    class FakeEvery:
        def __init__(self, interval):
            self.interval = interval

        @property
        def minutes(self):
            return self

        def do(self, fn):
            registered["interval"] = self.interval
            registered["fn"] = fn

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            thread_targets.append(self.target)

    monkeypatch.setattr(scheduler, "run_job_safely", lambda: ticks.append(1))
    monkeypatch.setattr(scheduler.schedule, "every", FakeEvery)
    monkeypatch.setattr(scheduler, "Thread", FakeThread)

    scheduler.start_scheduler()

    assert registered["interval"] == max(1, scheduler.PLAYBOOK_RUN_INTERVAL_IN_MINUTES)
    assert registered["fn"] is scheduler.run_job_safely
    assert len(thread_targets) == 1  # polling thread spawned (never runs: fake)
    assert ticks == [1]  # first tick ran inline after the thread started

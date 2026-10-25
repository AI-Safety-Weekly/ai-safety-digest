"""Exercise the actual watchdog shell with isolated GitHub responses."""
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


def run(event="schedule", conclusion="cancelled", status="completed", day="2026-10-25"):
    return dict(event=event, conclusion=conclusion, status=status,
                createdAt=f"{day}T10:17:00Z", startedAt=f"{day}T10:17:00Z",
                updatedAt=f"{day}T10:18:00Z")


def execute(tmp_path, runs, published=0):
    workflow = yaml.safe_load(Path(".github/workflows/watchdog.yml").read_text())
    script = workflow["jobs"]["check"]["steps"][0]["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in {
        "date": '#!/bin/sh\necho 2026-10-25\n',
        "gh": '''#!/bin/sh
case "$1 $2" in
  "run list") printf '%s\\n' "$TEST_RUNS" ;;
  "api "*) printf '%s\\n' "$TEST_PUBLISHED" ;;
  "workflow run") echo dispatched >> "$TEST_DISPATCH" ;;
  *) exit 99 ;;
esac
''',
    }.items():
        path = bin_dir / name
        path.write_text(source)
        path.chmod(0o755)
    dispatch = tmp_path / "dispatch"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               TEST_RUNS=json.dumps(runs), TEST_PUBLISHED=str(published),
               TEST_DISPATCH=str(dispatch), REPO="test/repo",
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary"))
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return dispatch.exists(), result.stdout


@pytest.mark.parametrize("conclusion", ["cancelled", "failure", "timed_out"])
def test_failed_schedule_gets_one_recovery(tmp_path, conclusion):
    dispatched, _ = execute(tmp_path, [run(conclusion=conclusion)])
    assert dispatched


@pytest.mark.parametrize("runs,published", [
    ([run(conclusion="success")], 0),
    ([run(status="in_progress", conclusion=None)], 0),
    ([run(status="queued", conclusion=None)], 0),
    ([], 1),
    ([run(), run(event="workflow_dispatch")], 0),
])
def test_success_active_publication_or_prior_recovery_prevents_dispatch(tmp_path, runs, published):
    assert not execute(tmp_path, runs, published)[0]


def test_dropped_schedule_recovers(tmp_path):
    assert execute(tmp_path, [run(day="2026-10-18")])[0]


def test_exhausted_recovery_is_not_reported_as_success(tmp_path):
    _, output = execute(tmp_path, [run(), run(event="workflow_dispatch")])
    assert "recovery attempt already used" in output

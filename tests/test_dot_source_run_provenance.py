"""Run the workflow's real provenance guard with synthetic HTTP responses only."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import urllib.request

import pytest
import yaml


INPUT_SHA = "a" * 40
CHECKPOINT_SHA = "b" * 40
REPOSITORY = "fixture/repository"


def workflow_guard():
    path = Path(__file__).parents[1] / ".github/workflows/dot-validation.yml"
    workflow = yaml.safe_load(path.read_text())
    step = next(step for step in workflow["jobs"]["validate"]["steps"]
                if step.get("name") == "Verify source run provenance")
    script = step["run"]
    assert script.startswith("python - <<'PYTHON'\n")
    assert script.rstrip().endswith("\nPYTHON")
    return compile(script.split("\n", 1)[1].rsplit("\nPYTHON", 1)[0], str(path), "exec")


@pytest.mark.parametrize("case", [
    "two_valid_sources", "no_sources", "failed_run", "incomplete_run",
    "wrong_commit", "wrong_repository", "wrong_workflow", "invalid_run_id",
    "name_without_run",
])
def test_source_run_provenance_guard_fails_closed(monkeypatch, case):
    env = {
        "GH_REPOSITORY": REPOSITORY,
        "GH_TOKEN": "synthetic-token-no-credentials",
        "INPUT_RUN": "123", "INPUT_NAME": "dot-handoff-" + INPUT_SHA,
        "CHECKPOINT_RUN": "456", "CHECKPOINT_NAME": "dot-handoff-" + CHECKPOINT_SHA,
    }
    runs = {
        "123": {"status": "completed", "conclusion": "success", "head_sha": INPUT_SHA,
                "repository": {"full_name": REPOSITORY},
                "path": ".github/workflows/dot-validation.yml"},
        "456": {"status": "completed", "conclusion": "success", "head_sha": CHECKPOINT_SHA,
                "repository": {"full_name": REPOSITORY},
                "path": ".github/workflows/dot-validation.yml"},
    }
    mutations = {
        "failed_run": {"conclusion": "failure"},
        "incomplete_run": {"status": "in_progress"},
        "wrong_commit": {"head_sha": "c" * 40},
        "wrong_repository": {"repository": {"full_name": "unrelated/repository"}},
        "wrong_workflow": {"path": ".github/workflows/unrelated.yml"},
    }
    # The enriched input is valid; the independent checkpoint must pass the
    # very same checks, rather than inheriting trust from the first source.
    runs["456"].update(copy.deepcopy(mutations.get(case, {})))
    if case == "no_sources":
        for key in ("INPUT_RUN", "INPUT_NAME", "CHECKPOINT_RUN", "CHECKPOINT_NAME"):
            env[key] = ""
    elif case == "invalid_run_id":
        env["CHECKPOINT_RUN"] = "../untrusted"
    elif case == "name_without_run":
        env["CHECKPOINT_RUN"] = ""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls = []
    def fake_urlopen(request, timeout):
        prefix = f"https://api.github.com/repos/{REPOSITORY}/actions/runs/"
        assert request.full_url.startswith(prefix)
        run_id = request.full_url.removeprefix(prefix)
        assert run_id in runs
        assert timeout == 30
        assert request.get_header("Authorization") == "Bearer synthetic-token-no-credentials"
        assert request.get_header("Accept") == "application/vnd.github+json"
        calls.append(run_id)
        return io.StringIO(json.dumps(runs[run_id]))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    code = workflow_guard()
    if case in {"two_valid_sources", "no_sources"}:
        exec(code, {})
        assert calls == (["123", "456"] if case == "two_valid_sources" else [])
    else:
        with pytest.raises(RuntimeError):
            exec(code, {})
        assert calls == (["123"] if case in {"invalid_run_id", "name_without_run"}
                         else ["123", "456"])

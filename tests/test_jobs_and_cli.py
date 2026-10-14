import json
import sys
import time

import pytest

from research_agent import cli
from research_agent.jobs import JobManager

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")


def test_run_python_returns_output(tmp_path):
    jobs = JobManager(tmp_path / "proj")
    result = jobs.run_python("print(6 * 7)", tmp_path / "ws", timeout=30)
    assert result["returncode"] == 0
    assert "42" in result["output"]
    assert jobs.list() == []  # finished snippets are not kept


def test_run_python_blocks_kill(tmp_path):
    result = JobManager(tmp_path).run_python("import os; os.kill(1, 9)", tmp_path)
    assert result["error"] == "BLOCKED"


def test_background_job_lifecycle(tmp_path):
    jobs = JobManager(tmp_path / "proj")
    started = jobs.start("echo hello; sleep 30", tmp_path, name="sleeper")
    job_id = started["job_id"]
    assert jobs.start("echo hello; sleep 30", tmp_path)["status"] == "ALREADY_RUNNING"

    status = jobs.status(job_id)
    assert status["state"] == "RUNNING_PRODUCING_OUTPUT"
    assert "hello" in status["log_tail"]

    refused = jobs.stop(job_id, reason="test")  # the agent may not stop a busy job
    assert refused["status"] == "REFUSED"
    assert jobs.stop(job_id, by_user=True)["status"] == "STOPPED"
    time.sleep(0.2)
    assert jobs.list()[0]["state"] in ("STOPPED", "FINISHED_ERROR")


def test_finished_job(tmp_path):
    jobs = JobManager(tmp_path / "proj")
    job_id = jobs.start("exit 3", tmp_path)["job_id"]
    assert jobs.status(job_id, wait_seconds=5)["state"] == "FINISHED_ERROR"


def test_cli_init_sources_index_search(tmp_path, notes_dir, capsys):
    assert cli.main(["init", str(tmp_path)]) == 0
    config_path = tmp_path / "research-agent.toml"
    assert config_path.exists() and (tmp_path / "core_notes.md").exists()

    config_path.write_text(
        f'[[sources]]\nname = "notes"\ntype = "text_folder"\npath = "{notes_dir}"\n'
    )
    assert cli.main(["-c", str(config_path), "index"]) == 0
    capsys.readouterr()
    assert cli.main(["-c", str(config_path), "search", "graphitization"]) == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits[0]["source"] == "notes"

    assert cli.main(["-c", str(config_path), "sources"]) == 0
    overview = json.loads(capsys.readouterr().out)
    assert overview[0]["status"]["documents"] == 2


def test_cli_reports_config_errors(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('[llm]\napi_key = "x"\n')
    assert cli.main(["-c", str(bad), "sources"]) == 2
    assert "Configuration error" in capsys.readouterr().err


def test_missing_api_key(monkeypatch):
    from research_agent.config import ConfigError, LLMConfig
    from research_agent.llm import create_client

    monkeypatch.delenv("TEST_KEY_THAT_IS_NOT_SET", raising=False)
    with pytest.raises(ConfigError, match="TEST_KEY_THAT_IS_NOT_SET"):
        create_client(LLMConfig(api_key_env="TEST_KEY_THAT_IS_NOT_SET"))

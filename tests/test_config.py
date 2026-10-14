from pathlib import Path

import pytest

from research_agent.config import ConfigError, load_config


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_AGENT_CONFIG", raising=False)
    config = load_config()
    assert config.path is None
    assert config.sources == []
    assert config.data_dir == tmp_path / "agent-data"
    assert config.llm.api_key_env == "DEEPSEEK_API_KEY"


def test_relative_paths_resolved_against_file(tmp_path):
    cfg = write(
        tmp_path / "research-agent.toml",
        """
        [paths]
        data_dir = "d"
        core_notes = "notes.md"
        [[sources]]
        name = "papers"
        type = "pdf_folder"
        path = "lib"
        script_dirs = ["w1", "w2"]
        """,
    )
    config = load_config(cfg)
    assert config.data_dir == tmp_path / "d"
    assert config.core_notes == tmp_path / "notes.md"
    source = config.sources[0]
    assert source.options["path"] == str(tmp_path / "lib")
    assert source.options["script_dirs"] == [str(tmp_path / "w1"), str(tmp_path / "w2")]


def test_api_key_in_file_is_rejected(tmp_path):
    cfg = write(tmp_path / "c.toml", '[llm]\napi_key = "secret"\n')
    with pytest.raises(ConfigError, match="API key"):
        load_config(cfg)


@pytest.mark.parametrize(
    "body, message",
    [
        ('[[sources]]\nname = "a"\n', "name' and 'type"),
        ('[[sources]]\nname = "a"\ntype = "x"\n[[sources]]\nname = "a"\ntype = "x"\n', "Duplicate"),
        ("[agent]\nunknown_key = 1\n", "Unknown key"),
        ("[llm\n", "Invalid TOML"),
    ],
)
def test_invalid_files(tmp_path, body, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path / "c.toml", body))


def test_disabled_source_kept_in_config(tmp_path):
    cfg = write(tmp_path / "c.toml", '[[sources]]\nname = "a"\ntype = "x"\nenabled = false\n')
    assert load_config(cfg).sources[0].enabled is False

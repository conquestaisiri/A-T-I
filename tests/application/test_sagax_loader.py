"""Tests for the Sagax secure key loader — never hardcodes, never logs."""

from __future__ import annotations

from pathlib import Path

from backend.infrastructure.secrets.sagax_loader import load_provider_keys


def write_keys(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_parses_mixed_providers_by_prefix(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    p = write_keys(
        tmp_path / "api_keys.env",
        "# comment\n"
        "GROQ=gsk_test1\n"
        "GROQ=gsk_test2\n"
        "OPENROUTER=sk-or-v1-test1\n"
        "OPENROUTER=sk-or-v1-test2\n"
        "GEMINI=AIza_test\n"
        "CEREBRAS=csk_test\n"
        "AGENT=sk-testagent\n",
    )
    pools = load_provider_keys(p)
    assert pools["groq"] == ["gsk_test1", "gsk_test2"]
    assert pools["openrouter"] == ["sk-or-v1-test1", "sk-or-v1-test2"]
    assert pools["gemini"] == ["AIza_test"]
    assert pools["cerebras"] == ["csk_test"]
    assert pools["agentrouter"] == ["sk-testagent"]


def test_env_wins_and_merges(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_env1,gsk_env2")
    p = write_keys(tmp_path / "api_keys.env", "GROQ=gsk_file1\n")
    pools = load_provider_keys(p)
    # Env first, then file appended, deduped
    assert pools["groq"] == ["gsk_env1", "gsk_env2", "gsk_file1"]


def test_missing_file_returns_env_only(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-envonly")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    pools = load_provider_keys(tmp_path / "nope.env")
    assert pools["openrouter"] == ["sk-or-v1-envonly"]
    assert "groq" not in pools


def test_deduplicates_preserving_order(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    p = write_keys(tmp_path / "api_keys.env", "GROQ=gsk_dup\nGROQ=gsk_dup\n")
    pools = load_provider_keys(p)
    assert pools["groq"] == ["gsk_dup"]


def test_unknown_prefix_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    p = write_keys(tmp_path / "api_keys.env", "UNKNOWN=foo\nGROQ=gsk_ok\n")
    pools = load_provider_keys(p)
    assert "groq" in pools
    assert pools["groq"] == ["gsk_ok"]
    assert "unknown" not in pools


def test_redact_key_never_logs_full_key():
    from backend.infrastructure.secrets.sagax_loader import redact_key

    fake = "gsk_" + "TESTKEY0000000000000000000000000000000000000000END"
    assert redact_key(fake) == "gsk_..." + fake[-4:]
    assert redact_key("sk-or-v1-test") == "sk-o...test"
    assert redact_key("short") == "***"
    full_redacted = redact_key(fake)
    assert "TESTKEY" not in full_redacted


def test_legacy_groq_env_without_suffix(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ", "gsk_legacy")
    pools = load_provider_keys(tmp_path / "empty.env")
    assert pools["groq"] == ["gsk_legacy"]


def test_sagax_keys_path_env_override(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    p = write_keys(tmp_path / "custom.env", "GROQ=gsk_custom\n")
    monkeypatch.setenv("SAGAX_KEYS_PATH", str(p))
    pools = load_provider_keys()
    assert pools["groq"] == ["gsk_custom"]


def test_real_sagax_file_loads_without_error(monkeypatch):
    # The operator's Sagax file at the default path should load if present;
    # this test just ensures the loader doesn't crash when the file exists.
    # We don't assert key values — that would echo secrets.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    default = Path(r"C:\Users\USER\Desktop\SagaxAI-API-Keys\api_keys.env")
    if not default.exists():
        return
    pools = load_provider_keys(default)
    # At least groq + openrouter should be present per the file's stated format
    assert "groq" in pools
    assert "openrouter" in pools
    assert len(pools["groq"]) >= 3
    assert len(pools["openrouter"]) >= 3

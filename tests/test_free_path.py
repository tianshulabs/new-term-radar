"""Free path: no AIsa key, and file-backed X posts."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar import __main__ as cli
from radar import aisa, breakout, sources


def test_aisa_has_key_and_llm_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(aisa.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("AISA_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("RADAR_LLM_API_KEY", raising=False)
    assert aisa.has_key() is False and aisa.llm_ready() is False
    monkeypatch.setenv("AISA_API_KEY", "k")
    assert aisa.has_key() is True and aisa.llm_ready() is True
    monkeypatch.delenv("AISA_API_KEY")
    monkeypatch.setenv("RADAR_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    assert aisa.llm_ready() is False
    monkeypatch.setenv("RADAR_LLM_API_KEY", "llm")
    assert aisa.has_key() is False and aisa.llm_ready() is True


def test_x_skips_cleanly_without_aisa_key_or_posts_file(cfg, tmp_path, monkeypatch):
    cfg["x_accounts"] = {"labs": ["SomeLab"], "bloggers": []}
    monkeypatch.delenv("AISA_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_X_POSTS_FILE", raising=False)
    monkeypatch.setattr(aisa.Path, "home", lambda: tmp_path)
    out = sources.x_new(cfg, tmp_path)
    assert out == {"skipped": True, "reason": "no AIsa key and no RADAR_X_POSTS_FILE"}


def test_x_reads_posts_from_json_file_without_aisa(cfg, tmp_path, monkeypatch):
    cfg["x_accounts"] = {"labs": ["a", "b"], "bloggers": []}
    monkeypatch.delenv("AISA_API_KEY", raising=False)
    monkeypatch.setattr(aisa.Path, "home", lambda: tmp_path)

    def forbidden(*a, **k):
        raise AssertionError("file-backed X must not call AIsa")
    monkeypatch.setattr(sources.aisa, "call_all", forbidden)

    created = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    posts_path = tmp_path / "x_posts.json"
    posts_path.write_text(json.dumps([
        {"account": "a", "text": "Strata is shipping today", "likes": 10, "created": created},
        {"userName": "b", "text": "Tried Strata — fast", "likeCount": 20, "createdAt": created},
    ]))
    monkeypatch.setenv("RADAR_X_POSTS_FILE", str(posts_path))
    # First run only builds vocabulary
    first = sources.x_new(cfg, tmp_path)
    assert first["source"] == "file" and first["first_run"] and first["terms"] == [] and first["posts"] == 2
    # Seed vocab without the new name, then a fresh dump should surface it
    from radar import snap
    snap.save(tmp_path / "x_vocab.json.gz", {"older": "2026-09-01"})
    later = sources.x_new(cfg, tmp_path)
    assert [r["term"] for r in later["terms"]] == ["strata"]
    assert later["source"] == "file" and later["cost_micros"] == 0


def test_x_reads_ndjson_and_config_x_posts_file(cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("AISA_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_X_POSTS_FILE", raising=False)
    monkeypatch.setattr(aisa.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sources.aisa, "call_all", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AIsa")))
    created = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp())
    dump = tmp_path / "posts.ndjson"
    dump.write_text(
        json.dumps({"account": "a", "text": "Qwen3.9 drops", "likes": 500, "created": created}) + "\n"
        + json.dumps({"account": "a", "text": "more on Qwen3.9", "likes": 50, "created": created}) + "\n"
    )
    cfg["x_accounts"] = {"labs": ["a"], "bloggers": []}
    cfg["x_posts_file"] = "posts.ndjson"   # relative to cfg["_dir"] == tmp_path
    from radar import snap
    snap.save(tmp_path / "x_vocab.json.gz", {})
    out = sources.x_new(cfg, tmp_path)
    assert out["source"] == "file" and "qwen3.9" in [r["term"] for r in out["terms"]]


def test_daily_without_aisa_runs_free_signals_and_notes_skips(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"lang": "en", "x_accounts": {"labs": ["a"], "bloggers": []}, "topics": {}}))
    monkeypatch.delenv("AISA_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_X_POSTS_FILE", raising=False)
    monkeypatch.delenv("RADAR_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("RADAR_LLM_API_KEY", raising=False)
    monkeypatch.setattr(aisa.Path, "home", lambda: tmp_path)

    monkeypatch.setattr(sources, "hf_new", lambda cfg, state: {
        "models": 2, "spaces": 1, "terms": [{"term": "musiccraft", "note": "HF #1", "examples": ["m/MusicCraft"]}]})
    monkeypatch.setattr(breakout, "breakouts", lambda cfg, state: {
        "searched": 3, "repos": [], "terms": [{"term": "gridcraft", "note": "breakout", "examples": ["o/gridcraft"], "repo": "o/gridcraft"}]})
    monkeypatch.setattr(cli.sitemaps, "diff", lambda *a: {"skipped": True})

    out = tmp_path / "report.md"
    # --new-sites/--asks/--sellers make weekly signals "due" so auto-skip reasons appear
    code = cli.main([
        "daily", "--config", str(cfg_path), "--state", str(tmp_path / "state"), "--out", str(out),
        "--new-sites", "--asks", "--sellers",
        "--skip", "sitemaps,gsc,research",
    ])
    assert code == 0
    text = out.read_text()
    assert "musiccraft" in text and "gridcraft" in text
    assert "no AIsa key" in text or "no AIsa key" in capsys.readouterr().err
    # X skipped cleanly (no crash)
    assert "① X" in text
    data = json.loads(out.with_suffix(".json").read_text())
    assert data["x"].get("skipped") is True
    assert "no AIsa key" in data["x"].get("reason", "")
    assert data["hf"]["terms"] and data["breakout"]["terms"]

"""Current overviews must not consume or relabel incremental discoveries."""
import io
import json
from datetime import datetime, timedelta, timezone

from radar import __main__ as cli
from radar import breakout, config, report, snap, sources, store


def test_x_overview_includes_seen_names_without_changing_vocabulary(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("AISA_API_KEY", "test-key")  # file path unused; allow AIsa branch under has_key()
    cfg.update(_overview=True, x_accounts={"labs": ["a"]})
    path = tmp_path / "x_vocab.json.gz"
    snap.save(path, {"qwen3.9": "2026-10-01", "older-name": "2026-09-01"})
    before = path.read_bytes()
    posted = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%a %b %d %H:%M:%S %z %Y")
    data = {"a": {"data": {"tweets": [{"text": "Qwen3.9 is fast", "likeCount": 500, "createdAt": posted}]}}}
    monkeypatch.setattr(sources.aisa, "call_all", lambda calls: (data, 3000))
    overview = sources.x_new(cfg, tmp_path)
    assert [r["term"] for r in overview["terms"]] == ["qwen3.9"]
    assert path.read_bytes() == before
    cfg["_overview"] = False
    assert sources.x_new(cfg, tmp_path)["terms"] == []


def test_hf_overview_shows_current_rank_without_consuming_new_models(cfg, tmp_path, monkeypatch):
    cfg["_overview"] = True
    path = tmp_path / "hf_top.json.gz"
    snap.save(path, ["maker/ExistingVision"])
    before = path.read_bytes()
    models = [{"id": "maker/ExistingVision"}, {"id": "maker/NewMusic"}]
    monkeypatch.setattr(sources.urllib.request, "urlopen", lambda *a, **kw: io.StringIO(json.dumps(models)))
    overview = sources.hf_new(cfg, tmp_path)
    assert [r["term"] for r in overview["terms"]] == ["existingvision", "newmusic"]
    assert all("currently #" in r["note"] for r in overview["terms"])
    assert path.read_bytes() == before
    cfg["_overview"] = False
    assert [r["term"] for r in sources.hf_new(cfg, tmp_path)["terms"]] == ["newmusic"]


def test_overview_with_no_history_does_not_create_a_baseline(cfg, tmp_path, monkeypatch):
    cfg["_overview"] = True
    monkeypatch.setattr(sources.urllib.request, "urlopen", lambda *a, **kw: io.StringIO('[{"id":"maker/ExampleVision"}]'))
    assert sources.hf_new(cfg, tmp_path)["terms"]
    assert not (tmp_path / "hf_top.json.gz").exists()
    cfg["_overview"] = False
    first = sources.hf_new(cfg, tmp_path)
    assert first["first_run"] and first["terms"] == []


def test_breakout_overview_preserves_flagged_history_and_newness(cfg, tmp_path, monkeypatch):
    cfg["_overview"] = True
    created = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    repos = [{"full_name": f"maker/{name}", "stargazers_count": 2000,
              "created_at": created, "description": "AI video model"} for name in ("Seen", "Fresh")]
    monkeypatch.setattr(breakout, "_get", lambda path: {"items": repos} if path.startswith('/search/') else {"name": "Maker"})
    stars, flagged = tmp_path / "gh_stars.json.gz", tmp_path / "gh_flagged.json.gz"
    snap.save(stars, {"maker/Seen": 1800})
    snap.save(flagged, {"maker/Seen": datetime.now(timezone.utc).date().isoformat()})
    before = (stars.read_bytes(), flagged.read_bytes())
    overview = breakout.breakouts(cfg, tmp_path)
    assert {r["term"] for r in overview["terms"]} == {"seen", "fresh"}
    assert next(r for r in overview["repos"] if r['repo'] == 'maker/Seen')["new"] is False
    assert (stars.read_bytes(), flagged.read_bytes()) == before
    cfg["_overview"] = False
    assert [r["term"] for r in breakout.breakouts(cfg, tmp_path)["terms"]] == ["fresh"]


def test_overview_command_is_labelled_and_does_not_pollute_reviews(tmp_path, monkeypatch):
    cfg_path, state, out = tmp_path / "config.json", tmp_path / "state", tmp_path / "report.md"
    cfg_path.write_text(json.dumps({"lang": "zh", "topics": {"音乐": "music"}}))
    monkeypatch.setattr(sources, "x_new", lambda cfg, state: {"skipped": True})
    monkeypatch.setattr(sources, "hf_new", lambda cfg, state: {"models": 1, "overview": cfg['_overview'], "terms": [
        {"term": "musiccraft", "note": "HF 当前榜单第 1 名", "examples": ["maker/MusicCraft"]}]})
    monkeypatch.setattr(breakout, "breakouts", lambda *a: {"searched": 0, "repos": [], "terms": []})
    def score(rows, model):
        for r in rows:
            r['named'] = 0.9
        return {"judged": len(rows)}
    monkeypatch.setattr(report.judge, "score", score)
    def forbidden(*a, **kw):
        raise AssertionError("An overview cannot write leads or run incremental-only signals")
    for mod, attr in ((store, 'record'), (cli.review, 'review'), (cli.sitemaps, 'diff'), (cli.new_sites, 'new_sites'), (cli.gsc, 'gsc_new'), (cli.revenue, 'products'), (cli.asks, 'asks'), (cli.sellers, 'sellers'), (cli.usecases, 'run'), (cli.pains, 'tutorial_asks')):
        monkeypatch.setattr(mod, attr, forbidden)
    cli.main(['daily', '--overview', '--revenue', '--asks', '--sellers', '--config', str(cfg_path), '--state', str(state), '--out', str(out)])
    text = out.read_text()
    assert '当前候选概览' in text and '不代表今天刚发布' in text and '**musiccraft**' in text
    assert '已对 1 个候选打分' in text and 'AssertionError' not in text
    data = json.loads(out.with_suffix('.json').read_text())
    assert data['mode'] == 'overview' and data['judge']['judged'] == 1
    assert store.connect(state).execute('SELECT COUNT(*) FROM leads').fetchone()[0] == 0


def test_empty_daily_report_explains_baseline_without_rendering_empty_table(tmp_path, monkeypatch):
    cfg_path, out = tmp_path / 'config.json', tmp_path / 'report.md'
    cfg_path.write_text('{"lang":"zh","topics":{"音乐":"music"}}')
    monkeypatch.setattr(sources, 'x_new', lambda *a: {'accounts': 1, 'answered': 1, 'posts': 20, 'first_run': True, 'terms': []})
    cli.main(['daily', '--config', str(cfg_path), '--state', str(tmp_path/'state'), '--out', str(out),
              '--skip', 'hf,breakout,sitemaps,newsites,gsc,review'])
    text = out.read_text()
    assert '正在建立首次基线' in text and 'daily --overview' in text
    assert '| 词 | 信号 |' not in text and '今天没有相关的新词' not in text


def test_hf_overview_preserves_space_snapshot(cfg, tmp_path, monkeypatch):
    cfg['_overview'] = True
    models = tmp_path / 'hf_top.json.gz'
    spaces = tmp_path / 'hf_spaces_top.json.gz'
    snap.save(models, ['maker/Existing'])
    snap.save(spaces, ['maker/OldDemo'])
    before = (models.read_bytes(), spaces.read_bytes())
    monkeypatch.setattr(sources, '_trending', lambda what: [{'id': 'maker/NewSpace-demo', 'sdk': 'gradio'}] if what == 'spaces' else [])
    result = sources.hf_new(cfg, tmp_path)
    assert result['terms'][0]['term'] == 'newspace'
    assert 'currently #' in result['terms'][0]['note']
    assert (models.read_bytes(), spaces.read_bytes()) == before
    cfg['_overview'] = False
    assert sources.hf_new(cfg, tmp_path)['terms'][0]['term'] == 'newspace'

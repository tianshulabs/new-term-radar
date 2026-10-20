import io
import json
import pytest
from radar import aisa

_POST = aisa._post


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    # These tests exercise transport with fake openers; retain the suite network guard elsewhere.
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network access in transport test")
    monkeypatch.setattr(aisa, '_post', _POST)
    monkeypatch.setattr(aisa.urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(aisa.urllib.request, 'build_opener', forbidden)


def test_local_judge_uses_separate_key_and_bypasses_proxy(monkeypatch):
    monkeypatch.setenv('AISA_API_KEY', 'aisa-data-only')
    monkeypatch.setenv('RADAR_LLM_BASE_URL', 'http://127.0.0.1:3425/v1/')
    monkeypatch.setenv('RADAR_LLM_API_KEY', 'local-only')
    calls = []
    class LocalOpener:
        def open(self, req, timeout):
            calls.append((req.full_url, req.get_header('Authorization'), json.loads(req.data)))
            return io.StringIO('{"choices":[{"message":{"content":"[]"}}]}')
    def build(handler):
        assert handler.proxies == {}
        return LocalOpener()
    monkeypatch.setattr(aisa.urllib.request, 'build_opener', build)
    assert aisa.chat('group/auto-gpt-6-luna', 'system', 'candidate') == '[]'
    assert calls[0][:2] == ('http://127.0.0.1:3425/v1/chat/completions', 'Bearer local-only')
    assert calls[0][2]['model'] == 'group/auto-gpt-6-luna'


def test_data_router_keeps_aisa_credentials_with_custom_llm(monkeypatch):
    monkeypatch.setenv('AISA_API_KEY', 'aisa-data-only')
    monkeypatch.setenv('RADAR_LLM_BASE_URL', 'http://127.0.0.1:3425/v1')
    monkeypatch.setenv('RADAR_LLM_API_KEY', 'local-only')
    seen = []
    def open_(req, timeout):
        seen.append((req.full_url, req.get_header('Authorization')))
        return io.StringIO('{"results":[]}')
    monkeypatch.setattr(aisa.urllib.request, 'urlopen', open_)
    assert aisa.call([{'call_id': 'test', 'tool': 'test', 'arguments': {}}]) == ({}, 0)
    assert seen == [(aisa.ROUTER, 'Bearer aisa-data-only')]


def test_custom_llm_missing_key_never_falls_back_to_aisa_key(monkeypatch):
    monkeypatch.setenv('AISA_API_KEY', 'aisa-data-only')
    monkeypatch.setenv('RADAR_LLM_BASE_URL', 'http://127.0.0.1:3425/v1')
    monkeypatch.delenv('RADAR_LLM_API_KEY', raising=False)
    with pytest.raises(aisa.AisaError, match='RADAR_LLM_API_KEY'):
        aisa.chat('model', 'system', 'candidate')


def test_default_judge_still_uses_aisa(monkeypatch):
    monkeypatch.delenv('RADAR_LLM_BASE_URL', raising=False)
    monkeypatch.setenv('AISA_API_KEY', 'aisa-data-only')
    seen = []
    def open_(req, timeout):
        seen.append((req.full_url, req.get_header('Authorization')))
        return io.StringIO('{"choices":[{"message":{"content":"[]"}}]}')
    monkeypatch.setattr(aisa.urllib.request, 'urlopen', open_)
    assert aisa.chat('model', 'system', 'candidate') == '[]'
    assert seen == [(aisa.CHAT, 'Bearer aisa-data-only')]

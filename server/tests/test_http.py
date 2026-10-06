import httpx
import pytest

from researcher import http
from researcher.http import HttpClient, HttpError


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(http, "DEFAULT_MIN_INTERVAL", 0.0)


def make_client(tmp_path, handler, **kw):
    return HttpClient(
        cache_path=tmp_path / "cache.db",
        backoff_base_s=0.0,
        transport=httpx.MockTransport(handler),
        **kw,
    )


def test_caches_by_url_and_params(tmp_path):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={"q": req.url.params.get("q")})

    with make_client(tmp_path, handler) as c:
        assert c.get_json("test", "https://x.test/a", {"q": "1"}) == {"q": "1"}
        assert c.get_json("test", "https://x.test/a", {"q": "1"}) == {"q": "1"}
        assert c.get_json("test", "https://x.test/a", {"q": "2"}) == {"q": "2"}
    assert len(calls) == 2


def test_cache_persists_across_clients(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={})

    with make_client(tmp_path, handler) as c:
        c.get("test", "https://x.test/a")
    with make_client(tmp_path, handler) as c:
        assert c.get("test", "https://x.test/a").from_cache
    assert len(calls) == 1


def test_post_body_is_part_of_cache_key(tmp_path):
    def handler(req):
        return httpx.Response(200, content=req.content)

    with make_client(tmp_path, handler) as c:
        assert c.post_json("test", "https://x.test/g", {"a": 1}) == {"a": 1}
        assert c.post_json("test", "https://x.test/g", {"a": 2}) == {"a": 2}


def test_reports_cache_hits_for_get_and_post(tmp_path):
    with make_client(tmp_path, lambda req: httpx.Response(200, json={})) as c:
        assert not c.get("test", "https://x.test/a").from_cache
        assert c.get("test", "https://x.test/a").from_cache
        assert not c.post("test", "https://x.test/g", {"a": 1}).from_cache
        assert c.post("test", "https://x.test/g", {"a": 1}).from_cache


def test_retries_then_succeeds(tmp_path):
    statuses = iter([503, 429, 200])

    def handler(req):
        return httpx.Response(next(statuses), json={"ok": True})

    with make_client(tmp_path, handler) as c:
        assert c.get_json("test", "https://x.test/r") == {"ok": True}


def test_gives_up_after_max_retries(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500)

    with make_client(tmp_path, handler, max_retries=2) as c:
        with pytest.raises(HttpError) as ei:
            c.get("test", "https://x.test/r")
    assert ei.value.status == 500
    assert len(calls) == 3


def test_client_error_not_retried_or_cached(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(404)

    with make_client(tmp_path, handler) as c:
        for _ in range(2):
            with pytest.raises(HttpError):
                c.get("test", "https://x.test/missing")
    assert len(calls) == 2

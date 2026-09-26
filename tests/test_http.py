"""The real Fetcher against a mock transport: pacing on a fake clock, bot challenges, quota exhaustion, outages, and statuses passed through."""

import httpx
import pytest

from fed_products import http as http_module
from fed_products.http import USER_AGENT, Blocked, Fetcher, PageMissing, QuotaExhausted, Unavailable

BOARD = "https://www.federalreserve.gov/monetarypolicy/beigebook202608-summary.htm"
MPLS = "https://www.minneapolisfed.org/beige-book-reports/1971/1971-06-su"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http_module.time, "sleep", lambda seconds: None)


def fetcher_with(handler, max_retries=5):
    fetcher = Fetcher(intervals={"www.federalreserve.gov": 0, "www.minneapolisfed.org": 0}, max_retries=max_retries)
    fetcher.client.close()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    return fetcher


def test_requests_to_one_host_are_a_second_apart_and_hosts_do_not_wait_for_each_other(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(http_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http_module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    fetcher = Fetcher()
    sends = []
    for host in ("www.federalreserve.gov", "www.minneapolisfed.org", "www.federalreserve.gov", "www.federalreserve.gov"):
        fetcher._pace(host)
        sends.append(clock[0])
    fetcher.client.close()
    assert sends == [1000.0, 1000.0, 1001.0, 1002.0]


def test_a_bot_challenge_raises_blocked_at_once():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(403, headers={"cf-mitigated": "challenge"}, text="<title>Just a moment...</title>")

    with pytest.raises(Blocked):
        fetcher_with(handler).get(BOARD)
    assert len(seen) == 1


def test_repeated_429_exhausts_the_host_and_later_calls_fail_fast():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "www.minneapolisfed.org":
            return httpx.Response(200)
        return httpx.Response(429, headers={"Retry-After": "0"})

    fetcher = fetcher_with(handler)
    with pytest.raises(QuotaExhausted):
        fetcher.get(BOARD)
    assert len(seen) == 3
    with pytest.raises(QuotaExhausted):
        fetcher.get(BOARD)
    assert len(seen) == 3, "a dead host gets no further requests"
    assert fetcher.get(MPLS).status_code == 200, "the other host is still used"


@pytest.mark.parametrize("failure", ["status", "transport"])
def test_server_errors_and_network_failures_become_unavailable_after_retries(failure):
    seen = []

    def handler(request):
        seen.append(request)
        if failure == "transport":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(503)

    fetcher = fetcher_with(handler, max_retries=2)
    with pytest.raises(Unavailable):
        fetcher.get(BOARD)
    assert len(seen) == 3
    assert "www.federalreserve.gov" not in fetcher.dead, "an outage is not remembered: the next unit tries again"


def test_statuses_below_500_come_back_and_headers_travel():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(304 if request.headers.get("If-Modified-Since") else 404)

    fetcher = fetcher_with(handler)
    assert fetcher.get(BOARD, headers={"If-Modified-Since": "Thu, 26 Feb 2026 16:43:11 GMT"}).status_code == 304
    assert fetcher.get(MPLS).status_code == 404
    assert seen[0].headers["If-Modified-Since"] == "Thu, 26 Feb 2026 16:43:11 GMT"
    assert seen[1].headers["User-Agent"] == USER_AGENT and "github.com/incrediblecrab" in USER_AGENT
    assert fetcher.requests == {"www.federalreserve.gov": 1, "www.minneapolisfed.org": 1}


def test_page_raises_on_anything_but_200():
    fetcher = fetcher_with(lambda request: httpx.Response(404 if "missing" in request.url.path else 200, content=b"<html>ok</html>"))
    assert fetcher.page(BOARD) == b"<html>ok</html>"
    with pytest.raises(PageMissing):
        fetcher.page("https://www.federalreserve.gov/missing.htm")

from __future__ import annotations

import httpx
import pytest

from trader_agent.client import EngineClient


@pytest.mark.parametrize("url", ["https://cloud-engine.example/", "http://127.0.0.1:9123/"])
async def test_client_sends_authenticated_request_to_given_url(url):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"THYAO": True})

    async with EngineClient(
        url=url, api_key="remote-key", transport=httpx.MockTransport(handle),
    ) as client:
        assert await client.prefetch_market() == {"THYAO": True}
    assert requests[0].method == "POST"
    assert str(requests[0].url) == url.rstrip("/") + "/v1/jobs/prefetch-market"
    assert requests[0].headers["Authorization"] == "Bearer remote-key"


@pytest.mark.parametrize("job", ["prefetch_market", "daily_digest"])
async def test_failed_job_is_not_reported_as_success(job):
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    async with EngineClient(url="http://engine.test", transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            await getattr(client, job)()
        assert error.value.response.status_code == 503

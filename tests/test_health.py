import pytest

async def test_health_ok(client):
    resp=await client.get("/api/v1/health")
    assert resp.status_code==200
    data=resp.json()
    assert data["status"]=="ok"

async def test_unknown_path_returns_unified_error(client):
    resp=await client.get("/api/v1/definitely-not-here")
    assert resp.status_code==404
    body=resp.json()
    assert "error" in body
    assert body["error"]["code"]=="not_found"

async def test_request_id_header_present(client):
    resp=await client.get("/api/v1/health")
    assert "X-Request-ID" in resp.headers 
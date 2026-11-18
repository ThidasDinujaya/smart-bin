import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

KEY = {"X-API-Key": "test-key"}
ADMIN = {"X-Admin-Token": "admin-secret"}
T0 = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)


# distance the sensor reads for a given fill %
def dist(fill, depth=80, dead=10):
    return round(depth - fill / 100 * (depth - dead), 2)


def make_client(tmp_path, **kw):
    cfg = Settings(db_path=str(tmp_path / "t.db"), api_key="test-key", admin_token="admin-secret",
                   **kw)
    return TestClient(create_app(cfg))


@pytest.fixture()
def client(tmp_path):
    with make_client(tmp_path) as c:
        c.post("/api/bins", headers=ADMIN, json={"id": "B1", "name": "Test Bin", "depth_cm": 80})
        yield c


def send(client, bin_id, fill, trigger="lid_close", hours=0.0, **extra):
    body = {"bin_id": bin_id, "distance_cm": dist(fill), "trigger": trigger,
            "timestamp": (T0 + timedelta(hours=hours)).isoformat(), **extra}
    r = client.post("/api/readings", headers=KEY, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_dashboard(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "SmartBin" in client.get("/").text


def test_device_auth_and_validation(client):
    body = {"bin_id": "B1", "distance_cm": 40}
    assert client.post("/api/readings", json=body).status_code == 401
    assert client.post("/api/readings", json=body, headers={"X-API-Key": "bad"}).status_code == 401
    assert client.post("/api/readings", json=body, headers=KEY).status_code == 200
    for bad in ({"bin_id": "bad id!", "distance_cm": 40}, {"bin_id": "B1", "distance_cm": -1},
                {"bin_id": "B1", "distance_cm": 40, "trigger": "polling"}):
        assert client.post("/api/readings", json=bad, headers=KEY).status_code == 422


def test_fill_is_calculated_and_unknown_bin_is_registered(client):
    assert send(client, "B1", 50)["fill_pct"] == 50.0
    assert send(client, "NEW-9", 20)["fill_pct"] == 20.0
    assert "NEW-9" in [b["id"] for b in client.get("/api/bins").json()]


def test_alert_lifecycle_nearly_full_full_collected(client):
    assert send(client, "B1", 50, hours=0)["event"] is None
    assert send(client, "B1", 82, hours=1)["event"] == "nearly_full"
    assert send(client, "B1", 97, hours=2)["event"] == "full"
    alerts = {a["kind"]: a for a in client.get("/api/alerts").json()}
    assert alerts["nearly_full"]["resolution"] == "escalated"
    assert alerts["full"]["resolved_at"] is None
    assert send(client, "B1", 8, hours=3)["event"] == "collected"
    assert client.get("/api/alerts?active_only=true").json() == []
    assert client.get("/api/summary").json()["collections_detected"] == 1
    assert {a["resolution"] for a in client.get("/api/alerts").json() if a["kind"] == "full"} == {"collected"}


def test_alert_clears_with_hysteresis(client):
    send(client, "B1", 85, hours=0)
    send(client, "B1", 75, hours=1)   # inside the band, alert stays
    assert len(client.get("/api/alerts?active_only=true").json()) == 1
    send(client, "B1", 60, hours=2)   # below the band, clears
    assert client.get("/api/alerts?active_only=true").json() == []


def test_sensor_fault_raised_and_cleared(client):
    r = client.post("/api/readings", headers=KEY, json={"bin_id": "B1", "distance_cm": 0.5}).json()
    assert r["status"] == "SENSOR_FAULT"
    assert client.get("/api/priority").json()["check_device"][0]["id"] == "B1"
    send(client, "B1", 30)
    assert client.get("/api/alerts?active_only=true").json() == []


def test_alert_actions_need_admin_token(client):
    send(client, "B1", 85)
    alert_id = client.get("/api/alerts").json()[0]["id"]
    assert client.post(f"/api/alerts/{alert_id}/ack").status_code == 401
    assert client.post(f"/api/alerts/{alert_id}/ack", headers=ADMIN).json()["status"] == "acknowledged"
    assert client.post(f"/api/alerts/{alert_id}/resolve", headers=ADMIN).json()["resolution"] == "operator"
    assert client.post("/api/alerts/999/ack", headers=ADMIN).status_code == 404


def test_refresh_command_flow(client):
    r = client.post("/api/bins/B1/refresh")
    assert r.status_code == 200
    cid = r.json()["command_id"]
    assert client.post("/api/bins/B1/refresh").json()["command_id"] == cid     # no duplicate while pending
    assert client.get(f"/api/commands/{cid}").json()["status"] == "pending"

    nxt = client.get("/api/bins/B1/commands/next?wait=0", headers=KEY).json()["command"]
    assert nxt == {"id": cid, "kind": "refresh"}
    assert client.get("/api/bins/B1/commands/next?wait=0", headers=KEY).json()["command"] is None
    assert client.get(f"/api/commands/{cid}").json()["status"] == "delivered"

    send(client, "B1", 64, trigger="refresh", command_id=cid)
    done = client.get(f"/api/commands/{cid}").json()
    assert done["status"] == "done" and done["fill_pct"] == 64.0
    view = [b for b in client.get("/api/bins").json() if b["id"] == "B1"][0]
    assert view["last_refresh"]["result_fill"] == 64.0 and view["pending_command"] is None


def test_refresh_cooldown_unknown_bin_and_device_auth(client):
    cid = client.post("/api/bins/B1/refresh").json()["command_id"]
    client.get("/api/bins/B1/commands/next?wait=0", headers=KEY)
    send(client, "B1", 40, trigger="refresh", command_id=cid)
    again = client.post("/api/bins/B1/refresh")
    assert again.status_code == 429 and "Retry-After" in again.headers
    assert client.post("/api/bins/NOPE/refresh").status_code == 404
    assert client.get("/api/bins/B1/commands/next").status_code == 401
    assert client.get("/api/bins/NOPE/commands/next", headers=KEY).status_code == 404


def test_long_poll_delivers_immediately_and_times_out(client):
    client.post("/api/bins/B1/refresh")
    start = time.monotonic()
    assert client.get("/api/bins/B1/commands/next?wait=5", headers=KEY).json()["command"] is not None
    assert time.monotonic() - start < 2          # came back at once
    start = time.monotonic()
    assert client.get("/api/bins/B1/commands/next?wait=1", headers=KEY).json()["command"] is None
    assert time.monotonic() - start >= 0.9       # nothing pending, so it waited


def test_unanswered_refresh_expires(tmp_path):
    with make_client(tmp_path, command_ttl_sec=0) as c:
        c.post("/api/bins", headers=ADMIN, json={"id": "B1", "name": "x"})
        cid = c.post("/api/bins/B1/refresh").json()["command_id"]
        time.sleep(1.1)
        assert c.get(f"/api/commands/{cid}").json()["status"] == "expired"


def test_offline_detection_and_recovery(client):
    send(client, "B1", 30)
    service = client.app.state.service
    assert service.check_offline(datetime.now(timezone.utc) + timedelta(hours=3)) == 1
    assert service.check_offline(datetime.now(timezone.utc) + timedelta(hours=3)) == 0   # no duplicate alert
    assert client.get("/api/alerts?active_only=true").json()[0]["kind"] == "offline"
    send(client, "B1", 31, trigger="heartbeat", hours=3)
    assert client.get("/api/alerts?active_only=true").json() == []


def test_priority_route_orders_by_urgency(client):
    for bid, name in (("B2", "Second"), ("B3", "Third")):
        client.post("/api/bins", headers=ADMIN, json={"id": bid, "name": name})
    send(client, "B1", 40)
    send(client, "B2", 85)
    send(client, "B3", 97)
    route = client.get("/api/priority").json()["collect"]
    assert [r["id"] for r in route] == ["B3", "B2"]
    assert route[0]["rank"] == 1 and "overflowing" in route[0]["reason"]


def test_forecast_hours_to_full(client):
    for i, fill in enumerate([10, 20, 30, 40, 50]):
        send(client, "B1", fill, trigger="lid_close", hours=i)
    view = [b for b in client.get("/api/bins").json() if b["id"] == "B1"][0]
    assert view["rate_pct_per_hour"] == 10.0
    assert view["hours_to_full"] == 4.5 and view["hours_to_alert"] == 3.0


def test_forecast_restarts_after_collection(client):
    for i, fill in enumerate([10, 30, 50, 70, 90]):
        send(client, "B1", fill, hours=i)
    send(client, "B1", 5, hours=5)               # emptied
    view = [b for b in client.get("/api/bins").json() if b["id"] == "B1"][0]
    assert view["hours_to_full"] is None          # too few readings yet
    assert view["collections"] == 1


def test_summary_shows_transmissions_saved(client):
    for minutes in (0, 15, 30, 45, 60):           # 5 readings in an hour
        send(client, "B1", 20, hours=minutes / 60)
    s = client.get("/api/summary").json()
    assert s["readings_sent"] == 5 and s["polling_equivalent"] == 61
    assert s["saved_pct"] == round((1 - 5 / 61) * 100, 1)
    assert s["readings_by_trigger"] == {"lid_close": 5}


def test_bin_management_requires_admin(client):
    body = {"id": "B9", "name": "Gate"}
    assert client.post("/api/bins", json=body).status_code == 401
    assert client.post("/api/bins", headers=ADMIN, json=body).status_code == 201
    assert client.post("/api/bins", headers=ADMIN, json=body).status_code == 409
    assert client.patch("/api/bins/B9", headers=ADMIN, json={"name": "Main Gate"}).json()["name"] == "Main Gate"
    assert client.patch("/api/bins/NOPE", headers=ADMIN, json={"name": "x"}).status_code == 404


def test_restart_keeps_state(tmp_path):
    cfg = Settings(db_path=str(tmp_path / "r.db"), api_key="test-key", admin_token="admin-secret")
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/bins").json() == []                # no fake bins
        c.post("/api/readings", headers=KEY, json={"bin_id": "Bin-01", "distance_cm": dist(90, 80)})
    with TestClient(create_app(cfg)) as c:
        bins = c.get("/api/bins").json()
        assert len(bins) == 1
        assert bins[0]["status"] == "NEARLY_FULL"
        assert c.get("/api/alerts?active_only=true").json()[0]["kind"] == "nearly_full"


def test_no_simulation_endpoints(client):
    assert client.get("/api/control").status_code in (404, 405)


def test_protected_dashboard_needs_token(tmp_path):
    with make_client(tmp_path, protect_dashboard=True) as c:
        c.post("/api/bins", headers=ADMIN, json={"id": "B1", "name": "Test Bin", "depth_cm": 80})
        assert c.get("/api/config").json()["dashboard_protected"] is True
        for path in ("/api/summary", "/api/bins", "/api/priority", "/api/alerts", "/api/bins/B1/readings"):
            assert c.get(path).status_code == 401
        assert c.post("/api/bins/B1/refresh").status_code == 401
        assert c.get("/api/bins", headers=ADMIN).status_code == 200      # admin token works
        assert c.get("/api/bins", headers=KEY).status_code == 200        # device key works
        assert c.get("/api/bins", headers={"X-Admin-Token": "wrong"}).status_code == 401
        assert c.post("/api/bins/B1/refresh", headers=ADMIN).status_code == 200
        assert c.get("/health").status_code == 200


def test_dashboard_open_by_default(client):
    assert client.get("/api/config").json()["dashboard_protected"] is False
    assert client.get("/api/bins").status_code == 200

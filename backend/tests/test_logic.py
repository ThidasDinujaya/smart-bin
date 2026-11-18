from app.logic import (fill_percent, fill_rate, hours_until, priority_key, priority_reason,
                       status_of, was_collected)


def test_fill_percent_empty_half_full():
    assert fill_percent(80, 80, 10) == 0.0      # sees the floor
    assert fill_percent(45, 80, 10) == 50.0     # halfway
    assert fill_percent(10, 80, 10) == 100.0    # at the full line
    assert fill_percent(5, 80, 10) == 100.0     # past the line is still 100


def test_fill_percent_rejects_impossible_readings():
    assert fill_percent(0.5, 80, 10) is None    # too close for the sensor
    assert fill_percent(200, 80, 10) is None    # way past the floor
    assert fill_percent(40, 10, 10) is None     # bad depth config


def test_fill_rate_linear():
    pts = [(0, 10), (1, 20), (2, 30), (3, 40), (4, 50)]
    assert round(fill_rate(pts), 2) == 10.0


def test_fill_rate_needs_enough_points_and_time():
    assert fill_rate([(0, 10), (1, 20)]) is None
    assert fill_rate([(0, 10), (0.05, 20), (0.1, 30), (0.15, 40)]) is None  # all within minutes


def test_hours_until():
    assert hours_until(95, 50, 10.0) == 4.5
    assert hours_until(95, 96, 10.0) == 0.0
    assert hours_until(95, 50, None) is None
    assert hours_until(95, 50, 0.0) is None     # not filling


def test_collection_detection():
    assert was_collected(90, 5, 30) is True
    assert was_collected(90, 70, 30) is False
    assert was_collected(None, 5, 30) is False


def test_status_precedence():
    f = lambda **k: status_of(k.get("fill", 10), 80, 95, k.get("data", True), k.get("fault", False), k.get("off", False))
    assert f(data=False) == "NO_DATA"
    assert f(fault=True, fill=99) == "SENSOR_FAULT"
    assert f(off=True, fill=99) == "OFFLINE"
    assert f(fill=96) == "FULL"
    assert f(fill=85) == "NEARLY_FULL"
    assert f(fill=40) == "OK"


def test_priority_order_and_reason():
    full = {"status": "FULL", "hours_to_full": 0.0, "fill_pct": 97}
    near = {"status": "NEARLY_FULL", "hours_to_full": 1.0, "fill_pct": 85}
    soon = {"status": "OK", "hours_to_full": 3.0, "fill_pct": 60}
    ordered = sorted([soon, near, full], key=priority_key)
    assert ordered == [full, near, soon]
    assert "full in about 1.0 h" in priority_reason(near)

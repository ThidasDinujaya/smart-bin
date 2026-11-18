# pure helpers, no db and no web
import statistics
from typing import List, Optional, Tuple

MIN_VALID_CM = 2.0  # sensor can't read closer than this


# distance to fill %, None when the reading is impossible
def fill_percent(distance_cm: float, depth_cm: float, dead_zone_cm: float) -> Optional[float]:
    if distance_cm < MIN_VALID_CM or distance_cm > depth_cm + 20:
        return None
    usable = depth_cm - dead_zone_cm
    if usable <= 0:
        return None
    pct = (depth_cm - distance_cm) / usable * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


# fill slope in percent per hour from recent points
def fill_rate(points: List[Tuple[float, float]], min_points: int = 4, max_points: int = 12,
              min_span_h: float = 0.5) -> Optional[float]:
    pts = points[-max_points:]
    if len(pts) < min_points:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if max(xs) - min(xs) < min_span_h:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den < 1e-9:
        return None
    return sum((x - mx) * (y - my) for x, y in pts) / den


# hours until the threshold, None if unknown or not filling
def hours_until(threshold: float, current: float, rate: Optional[float]) -> Optional[float]:
    if rate is None or rate <= 0.05:
        return None
    if current >= threshold:
        return 0.0
    return round((threshold - current) / rate, 1)


# a big drop between readings means the bin was emptied
def was_collected(prev_fill: Optional[float], new_fill: Optional[float], drop_pct: float) -> bool:
    return prev_fill is not None and new_fill is not None and (prev_fill - new_fill) >= drop_pct


def status_of(fill: Optional[float], alert_pct: float, full_pct: float,
              has_data: bool, sensor_fault: bool, offline: bool) -> str:
    if not has_data:
        return "NO_DATA"
    if sensor_fault:
        return "SENSOR_FAULT"
    if offline:
        return "OFFLINE"
    if fill is None:
        return "NO_DATA"
    if fill >= full_pct:
        return "FULL"
    if fill >= alert_pct:
        return "NEARLY_FULL"
    return "OK"


# sort key for the route, fullest and soonest first
def priority_key(view: dict):
    order = {"FULL": 0, "NEARLY_FULL": 1}.get(view["status"], 2)
    eta = view["hours_to_full"] if view["hours_to_full"] is not None else 999.0
    return (order, eta, -(view["fill_pct"] or 0))


def priority_reason(view: dict) -> str:
    text = f"{view['fill_pct']:.0f}% full"
    if view["status"] == "FULL":
        return text + ", overflowing soon"
    if view["hours_to_full"] is not None:
        text += f", full in about {view['hours_to_full']} h"
    return text

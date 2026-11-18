"""Runtime-configurable detection settings stored in the database (system_settings table)."""
import copy

from sqlalchemy.orm import Session

from app.models import SystemSetting

DEFAULT_DETECTION = {
    "reporting_threshold": 10000.0,       # currency-transaction reporting threshold being evaded
    "structuring_band": 0.15,             # amounts within 15% below threshold are 'near-threshold'
    "structuring_min_count": 3,
    "structuring_window_days": 7,
    "smurfing_min_senders": 3,
    "rapid_window_hours": 48,
    "rapid_min_ratio": 0.7,               # >=70% of an inbound amount forwarded
    "rapid_min_amount": 2000.0,
    "velocity_window_minutes": 60,
    "velocity_min_count": 8,
    "cycle_max_length": 6,
    "cycle_max_span_days": 14,
    "cycle_amount_tolerance": 0.35,       # each hop within 35% of the cycle's first amount
    "dormant_days": 180,
    "dormant_min_amount": 5000.0,
    "unusual_zscore": 4.0,
    "unusual_min_history": 8,
    "network_min_shared_counterparties": 3,
    "network_min_jaccard": 0.6,
    "ml_contamination": 0.02,
    "ml_weight": 0.35,                    # max contribution of the anomaly model to the 0-100 risk score
    "flag_threshold": 50.0,               # transactions at/above this risk score are flagged for review
}

DEFAULTS = {"detection": DEFAULT_DETECTION}


def get_setting(db: Session, key: str) -> dict:
    row = db.get(SystemSetting, key)
    merged = copy.deepcopy(DEFAULTS.get(key, {}))
    if row:
        merged.update(row.value or {})
    return merged


def update_setting(db: Session, key: str, values: dict, user_id: int | None) -> dict:
    current = get_setting(db, key)
    allowed = DEFAULTS.get(key)
    if allowed is not None:
        unknown = set(values) - set(allowed)
        if unknown:
            raise ValueError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
        for k, v in values.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                raise ValueError(f"Setting '{k}' must be a non-negative number")
    current.update(values)
    row = db.get(SystemSetting, key)
    if row:
        row.value, row.updated_by_id = current, user_id
    else:
        db.add(SystemSetting(key=key, value=current, updated_by_id=user_id))
    return current

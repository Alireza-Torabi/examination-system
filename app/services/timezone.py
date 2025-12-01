import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import Config


TIMEZONE_OPTIONS = Config.TIMEZONE_OPTIONS


def to_local(dt: datetime | None, tz_name: str) -> datetime | None:
    if not dt:
        return dt
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pragma: no cover
        tz = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def local_to_utc(dt: datetime | None, tz_name: str) -> datetime | None:
    if not dt:
        return dt
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pragma: no cover
        tz = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_datetime_local_input(dt: datetime | None, tz_name: str) -> str:
    if not dt:
        return ""
    local = to_local(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc), tz_name)
    return local.strftime("%Y-%m-%dT%H:%M")


def is_rtl_text(text: str | None) -> bool:
    if not text:
        return False
    return bool(re.search(r"[\u0600-\u06FF]", text))

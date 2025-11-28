import os
from pathlib import Path
from zoneinfo import available_timezones

try:
    import pytz
except ImportError:  # pragma: no cover
    pytz = None


def _timezone_options() -> list[str]:
    options = sorted(available_timezones())
    if not options and pytz:
        options = pytz.all_timezones
    if not options:
        # Minimal fallback if neither system tzdata nor pytz is available.
        options = [
            "Asia/Tehran",
            "UTC",
            "Europe/London",
            "America/New_York",
            "Asia/Dubai",
            "Asia/Karachi",
            "Asia/Kolkata",
            "Asia/Tokyo",
            "Australia/Sydney",
        ]
    return options


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
TIMEZONE_OPTIONS = _timezone_options()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("SQLALCHEMY_DATABASE_URI")
        or "sqlite:///exam_app.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", DEFAULT_UPLOAD_FOLDER)
    TIMEZONE_OPTIONS = TIMEZONE_OPTIONS

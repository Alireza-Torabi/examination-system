from functools import wraps

from flask import abort, flash, redirect, session, url_for

from app.extensions import db
from app.models import User


def get_current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.session.get(User, uid)


def login_required(role=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                flash("Please log in first.")
                return redirect(url_for("auth.login"))
            if role:
                allowed = {role} if isinstance(role, str) else set(role)
                if user.role != "admin" and user.role not in allowed:
                    abort(403)
            return func(*args, **kwargs)

        return wrapper

    return decorator

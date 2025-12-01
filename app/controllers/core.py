from flask import Blueprint, redirect, request, url_for

from app.extensions import db
from app.models import AccessLog
from app.utils.auth import get_current_user

bp = Blueprint("core", __name__)


@bp.before_app_request
def log_access():
    # Skip logging static files
    if request.path.startswith("/static"):
        return
    user = get_current_user()
    tenant_id = user.tenant_id if user else None
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    try:
        entry = AccessLog(
            ip=ip,
            path=request.path[:400],
            method=request.method,
            user_agent=(request.headers.get("User-Agent") or "")[:400],
            user_id=user.id if user else None,
            tenant_id=tenant_id,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()


@bp.route("/")
def index():
    user = get_current_user()
    if user:
        if user.role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("instructor.instructor_dashboard" if user.role == "instructor" else "student.student_dashboard"))
    return redirect(url_for("auth.login"))

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for

from app.extensions import db
from app.models import AccessLog, Attempt, Exam, ExamDeletionLog, Tenant, User
from app.services.backup import (
    BackupError,
    backup_folder,
    create_backup_archive,
    list_backups,
    purge_all_data,
    restore_backup_file,
    restore_backup_upload,
)
from app.services.timezone import TIMEZONE_OPTIONS, fmt_dt, to_local
from app.utils.auth import get_current_user, login_required

bp = Blueprint("admin", __name__)


def get_tenants_for_forms():
    return Tenant.query.order_by(Tenant.name).all()


@bp.route("/admin")
@login_required(role="admin")
def admin_dashboard():
    tenant_filter = request.args.get("tenant_id", type=int)
    exam_filter = request.args.get("exam_id", type=int)
    tenants = Tenant.query.order_by(Tenant.name).all()
    admin_user = get_current_user()
    admin_tz = admin_user.timezone or "UTC"

    stats = {
        "tenants": Tenant.query.count(),
        "users": User.query.count(),
        "exams": Exam.query.filter(Exam.deleted_at.is_(None)).count(),
        "attempts": Attempt.query.count(),
    }

    users = User.query.order_by(User.tenant_id, User.role, User.username).all()

    exams_query = Exam.query.order_by(Exam.start_at.desc())
    if tenant_filter:
        exams_query = exams_query.filter_by(tenant_id=tenant_filter)
    exams = exams_query.filter(Exam.deleted_at.is_(None)).all()

    exam_options = Exam.query.order_by(Exam.title).all()

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        tenants=tenants,
        users=users,
        exams=exams,
        tenant_filter=tenant_filter,
        exam_filter=exam_filter,
        exam_options=exam_options,
        admin_tz=admin_tz,
    )


@bp.route("/logs")
@login_required(role="admin")
def admin_logs():
    admin_user = get_current_user()
    admin_tz = admin_user.timezone or "UTC"
    view = request.args.get("view", "app")
    access_logs = []
    deletion_logs = []
    attempt_logs = []

    if view == "access":
        raw_access = (
            AccessLog.query.order_by(AccessLog.created_at.desc())
            .limit(200)
            .all()
        )
        for log in raw_access:
            access_logs.append(
                {
                    "time": fmt_dt(to_local(log.created_at, admin_tz)),
                    "ip": log.ip,
                    "path": log.path,
                    "method": log.method,
                    "user": log.user,
                    "ua": log.user_agent,
                }
            )
    else:
        deletion_raw = ExamDeletionLog.query.order_by(ExamDeletionLog.deleted_at.desc()).limit(200).all()
        deletion_logs = [
            {"obj": log, "deleted_local": fmt_dt(to_local(log.deleted_at, admin_tz))}
            for log in deletion_raw
        ]
        attempt_raw = Attempt.query.order_by(Attempt.submitted_at.desc().nullslast()).limit(200).all()
        for att in attempt_raw:
            attempt_logs.append(
                {
                    "obj": att,
                    "started_local": fmt_dt(to_local(att.started_at, admin_tz)),
                    "submitted_local": fmt_dt(to_local(att.submitted_at, admin_tz)) if att.submitted_at else None,
                }
            )

    return render_template(
        "logs.html",
        access_logs=access_logs,
        deletion_logs=deletion_logs,
        attempt_logs=attempt_logs,
        view=view,
    )


@bp.route("/admin/backups")
@login_required(role="admin")
def admin_backups():
    admin_user = get_current_user()
    admin_tz = admin_user.timezone or "UTC"
    backups = []
    for entry in list_backups():
        created_local = fmt_dt(to_local(entry["created_at"], admin_tz))
        size_kb = round(entry["size"] / 1024, 1) if entry["size"] else 0
        backups.append(
            {
                "name": entry["name"],
                "size_kb": size_kb,
                "created_local": created_local,
            }
        )
    return render_template("admin_backups.html", backups=backups, admin_tz=admin_tz)


@bp.route("/admin/backups/create", methods=["POST"])
@login_required(role="admin")
def admin_backup_create():
    try:
        create_backup_archive(persist=True)
        flash("Backup created.")
    except BackupError as exc:
        flash(str(exc))
        return redirect(url_for("admin.admin_backups"))
    except Exception:  # pylint: disable=broad-except
        current_app.logger.exception("Backup failed")
        flash("Backup failed. Please check the server logs.")
        return redirect(url_for("admin.admin_backups"))

    return redirect(url_for("admin.admin_backups"))


@bp.route("/admin/backups/<path:filename>/download")
@login_required(role="admin")
def admin_backup_download(filename):
    folder = backup_folder()
    requested = (folder / filename).resolve()
    try:
        requested.relative_to(folder)
    except ValueError:
        abort(404)
    if not requested.exists():
        abort(404)
    return send_file(
        requested,
        as_attachment=True,
        download_name=requested.name,
        mimetype="application/zip",
        max_age=0,
    )


@bp.route("/admin/backups/restore", methods=["POST"])
@login_required(role="admin")
def admin_backup_restore():
    file_obj = request.files.get("backup_file")
    try:
        result = restore_backup_upload(file_obj)
        flash(
            f"Restore complete. Database: {result.get('database', 'unknown')}; uploads restored: {result.get('uploads_restored', 0)}."
        )
    except BackupError as exc:
        flash(str(exc))
    except Exception:  # pylint: disable=broad-except
        current_app.logger.exception("Restore failed")
        flash("Restore failed. Please check the server logs.")
    return redirect(url_for("admin.admin_backups"))


@bp.route("/admin/backups/<path:filename>/restore", methods=["POST"])
@login_required(role="admin")
def admin_backup_restore_file(filename):
    confirm_text = request.form.get("confirm_text", "").strip()
    if confirm_text != "RESTORE":
        flash("Type RESTORE to confirm restore.")
        return redirect(url_for("admin.admin_backups"))
    folder = backup_folder()
    requested = (folder / filename).resolve()
    try:
        requested.relative_to(folder)
    except ValueError:
        abort(404)
    if not requested.exists():
        abort(404)
    try:
        result = restore_backup_file(requested)
        flash(
            f"Restore from {requested.name} complete. Database: {result.get('database', 'unknown')}; uploads restored: {result.get('uploads_restored', 0)}."
        )
    except BackupError as exc:
        flash(str(exc))
    except Exception:  # pylint: disable=broad-except
        current_app.logger.exception("Restore failed")
        flash("Restore failed. Please check the server logs.")
    return redirect(url_for("admin.admin_backups"))


@bp.route("/admin/backups/reset", methods=["POST"])
@login_required(role="admin")
def admin_backup_reset():
    confirm_text = request.form.get("confirm_text", "").strip()
    if confirm_text != "RESET":
        flash("Type RESET to confirm factory reset.")
        return redirect(url_for("admin.admin_backups"))
    try:
        result = purge_all_data()
        flash(
            f"Factory reset complete. Database recreated and uploads cleared (items removed: {result.get('uploads_removed', 0)})."
        )
    except BackupError as exc:
        flash(str(exc))
    except Exception:  # pylint: disable=broad-except
        current_app.logger.exception("Factory reset failed")
        flash("Factory reset failed. Please check the server logs.")
    return redirect(url_for("admin.admin_backups"))


@bp.route("/admin/users/new", methods=["GET", "POST"])
@login_required(role="admin")
def admin_user_new():
    tenants = get_tenants_for_forms()
    instructors = User.query.filter_by(role="instructor").order_by(User.username).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role", "student")
        password = request.form.get("password", "")
        tenant_id = request.form.get("tenant_id", type=int)
        instructor_id = request.form.get("instructor_id", type=int)
        timezone = request.form.get("timezone", "").strip() or None
        if not username or not password or not tenant_id:
            flash("Username, password, and tenant are required.")
            return redirect(request.url)
        if role not in {"admin", "instructor", "student"}:
            role = "student"
        if User.query.filter_by(username=username).first():
            flash("Username already exists.")
            return redirect(request.url)
        if role == "student" and instructor_id:
            instructor = db.session.get(User, instructor_id)
            if not instructor or instructor.role != "instructor" or instructor.tenant_id != tenant_id:
                flash("Instructor must be a valid instructor in the same tenant.")
                return redirect(request.url)
        from werkzeug.security import generate_password_hash

        db.session.add(
            User(
                username=username,
                full_name=full_name,
                role=role,
                password_hash=generate_password_hash(password),
                tenant_id=tenant_id,
                instructor_id=instructor_id if role == "student" else None,
                timezone=timezone,
            )
        )
        db.session.commit()
        flash("User created.")
        return redirect(url_for("admin.admin_dashboard"))
    return render_template("user_form.html", tenants=tenants, user=None, instructors=instructors, timezone_options=TIMEZONE_OPTIONS)


@bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required(role="admin")
def admin_user_edit(user_id):
    user_obj = db.session.get(User, user_id)
    if not user_obj:
        abort(404)
    tenants = get_tenants_for_forms()
    instructors = User.query.filter_by(role="instructor").order_by(User.username).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if not username:
            flash("Username is required.")
            return redirect(request.url)
        existing = User.query.filter(User.username == username, User.id != user_obj.id).first()
        if existing:
            flash("Username already exists.")
            return redirect(request.url)
        user_obj.username = username
        user_obj.full_name = request.form.get("full_name", "").strip()
        user_obj.role = request.form.get("role", user_obj.role)
        tenant_id = request.form.get("tenant_id", type=int) or user_obj.tenant_id
        user_obj.tenant_id = tenant_id
        instructor_id = request.form.get("instructor_id", type=int)
        user_obj.timezone = request.form.get("timezone", "").strip() or None
        if user_obj.role == "student" and instructor_id:
            instructor = db.session.get(User, instructor_id)
            if not instructor or instructor.role != "instructor" or instructor.tenant_id != tenant_id:
                flash("Instructor must be a valid instructor in the same tenant.")
                return redirect(request.url)
            user_obj.instructor_id = instructor_id
        else:
            user_obj.instructor_id = None
        new_password = request.form.get("password", "")
        confirm_pw = request.form.get("password_confirm", "")
        if new_password:
            if confirm_pw and new_password != confirm_pw:
                flash("Passwords do not match.")
                return redirect(request.url)
            from werkzeug.security import generate_password_hash

            user_obj.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash("User updated.")
        return redirect(url_for("admin.admin_dashboard"))
    return render_template("user_form.html", tenants=tenants, user=user_obj, instructors=instructors, timezone_options=TIMEZONE_OPTIONS)


@bp.route("/admin/tenants/new", methods=["GET", "POST"])
@login_required(role="admin")
def admin_tenant_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip()
        if not name or not slug:
            flash("Name and slug are required.")
            return redirect(request.url)
        if Tenant.query.filter_by(slug=slug).first():
            flash("Slug already exists.")
            return redirect(request.url)
        db.session.add(Tenant(name=name, slug=slug))
        db.session.commit()
        flash("Tenant created.")
        return redirect(url_for("admin.admin_dashboard"))
    return render_template("tenant_form.html")

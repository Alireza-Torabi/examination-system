from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import User
from app.services.timezone import TIMEZONE_OPTIONS
from app.utils.auth import get_current_user, login_required
from werkzeug.security import generate_password_hash

bp = Blueprint("settings", __name__)


@bp.route("/settings", methods=["GET", "POST"])
@login_required()
def settings():
    user = get_current_user()
    if request.method == "POST":
        tz = request.form.get("timezone", "").strip() or None
        user.timezone = tz
        db.session.commit()
        flash("Settings saved.")
        return redirect(url_for("settings.settings"))
    return render_template("settings.html", user=user, timezone_options=TIMEZONE_OPTIONS)


@bp.route("/settings/password", methods=["GET", "POST"])
@login_required()
def change_password():
    user = get_current_user()
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if not new_pw:
            flash("New password is required.")
            return redirect(request.url)
        if not user.check_password(current_pw):
            flash("Current password is incorrect.")
            return redirect(request.url)
        if not confirm_pw or new_pw != confirm_pw:
            flash("Passwords do not match.")
            return redirect(request.url)
        user.password_hash = generate_password_hash(new_pw)
        db.session.commit()
        flash("Password updated.")
        return redirect(url_for("settings.settings"))
    return render_template("change_password.html", show_current=True)

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.models import User
from app.utils.auth import get_current_user

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            flash("Welcome back.")
            return redirect(url_for("core.index"))
        flash("Invalid credentials.")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logged out.")
    return redirect(url_for("auth.login"))

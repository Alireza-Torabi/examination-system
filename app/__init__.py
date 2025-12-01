import os
from pathlib import Path

from flask import Flask

from .config import BASE_DIR, Config
from .extensions import db


def create_app(config_class: type[Config] = Config) -> Flask:
    """Application factory to configure Flask, extensions, and shared paths."""
    templates_dir = Path(BASE_DIR) / "templates"
    static_dir = Path(BASE_DIR) / "static"
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder=str(templates_dir),
        static_folder=str(static_dir),
    )
    app.config.from_object(config_class)

    # Ensure upload directory exists early.
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)

    # Register blueprints
    from app.controllers import auth, core, admin, instructor, student, settings as settings_ctrl

    app.register_blueprint(core.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(instructor.bp)
    app.register_blueprint(student.bp)
    app.register_blueprint(settings_ctrl.bp)

    # Template filters
    from app.utils.helpers import normalize_imgs, img_url
    from app.utils.auth import get_current_user
    from app.services.timezone import is_rtl_text

    app.add_template_filter(normalize_imgs, "normalize_imgs")
    app.add_template_filter(img_url, "img_url")

    @app.context_processor
    def inject_user():
        return {"current_user": get_current_user(), "is_rtl": is_rtl_text}

    # CLI command for init db
    from app.services.migrations import init_db, migrate_schema

    @app.cli.command("initdb")
    def initdb_command():
        with app.app_context():
            init_db()

    with app.app_context():
        migrate_schema()

    return app

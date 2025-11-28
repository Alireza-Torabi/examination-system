import os
from pathlib import Path

from flask import Flask

from .config import Config, BASE_DIR
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

    return app

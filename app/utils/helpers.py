import glob
import os
import re
from datetime import datetime

from flask import current_app, url_for
from werkzeug.utils import secure_filename

ALLOWED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def parse_datetime(value: str):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def save_image_file(image_file):
    """Store an uploaded image and return its relative static path."""
    if not image_file or not image_file.filename:
        return None
    filename = secure_filename(image_file.filename)
    if not filename.lower().endswith(ALLOWED_IMAGE_EXTENSIONS):
        raise ValueError("Only image files are allowed (png, jpg, jpeg, gif, webp).")
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)
    stored_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{filename}"
    file_path = os.path.join(upload_dir, stored_name)
    image_file.save(file_path)
    return f"uploads/{stored_name}"


def normalize_imgs(html: str | None) -> str | None:
    """Normalize relative image sources (uploads/*) to absolute /static paths for rendering."""
    if not html:
        return html

    def _fix(match):
        quote = match.group(1)
        path = match.group(2).lstrip("/")
        if path.startswith("instructor/"):
            path = path[len("instructor/") :]
        if path.startswith("static/"):
            path = path[len("static/") :]
        if not path.startswith("uploads/"):
            path = f"uploads/{path}"
        return f'src={quote}/static/{path}{quote}'

    fixed = (
        html.replace('src="uploads/', 'src="/static/uploads/')
        .replace("src='uploads/", "src='/static/uploads/")
        .replace('src="static/uploads/', 'src="/static/uploads/')
        .replace("src='static/uploads/", "src='/static/uploads/")
        .replace('src="/static/static/uploads/', 'src="/static/uploads/')
    )
    fixed = re.sub(r"src=(['\"])(?!https?:|data:|/)([^'\"\s>]+)\1", _fix, fixed, flags=re.IGNORECASE)
    return fixed


def img_url(path: str | None) -> str | None:
    """Build a usable URL for stored images."""
    if not path:
        return None
    pth = str(path).replace("\\", "/").strip()
    if pth.startswith(("http://", "https://", "data:", "/")):
        return pth
    if pth.startswith("static/"):
        pth = pth[len("static/") :]
    if pth.startswith("/static/"):
        pth = pth[len("/static/") :]
    rel = pth.lstrip("/")
    if not rel.startswith("uploads/"):
        rel = f"uploads/{rel}"
    static_folder = current_app.static_folder
    abs_candidate = os.path.join(static_folder, rel)
    if os.path.exists(abs_candidate):
        return url_for("static", filename=rel)
    tail = os.path.basename(rel)
    fallback_matches = glob.glob(os.path.join(static_folder, "uploads", f"*{tail}"))
    if fallback_matches:
        alt_rel = f"uploads/{os.path.basename(fallback_matches[0])}"
        return url_for("static", filename=alt_rel)
    return None

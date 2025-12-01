# Exam App (Flask) – MVC Structure

This app serves multi-tenant instructor/student exams with dashboards for admin, instructors, and students. It now uses an MVC-style layout with blueprints, models, and services under `app/`.

## Quick start (local)
1. **Clone & enter repo**
   ```bash
   cd /path/to/Exam
   ```
2. **Create virtualenv & install deps**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Initialize the database (SQLite by default)**
   ```bash
   python app.py initdb
   # or: flask --app app initdb
   ```
4. **Run the dev server**
   ```bash
   python app.py
   # or: flask --app app run --debug --host 0.0.0.0 --port 5000
   ```
5. **Default logins**
- Admin: `admin` / `admin123`
- Instructor: `instructor` / `instructor123`
- Student: `student1` / `student123`

## Configuration
- `SECRET_KEY` (default: `dev-secret-key`)
- `DATABASE_URL` or `SQLALCHEMY_DATABASE_URI` (default: `sqlite:///exam_app.db`)
- `UPLOAD_FOLDER` (default: `./static/uploads`)
- `BACKUP_FOLDER` (default: `./instance/backups`)
- `PORT` (for `python app.py`)
- Timezone options are auto-loaded from system tzdata/pytz; fallback list is defined in `app/config.py`.

## Backups
- Admins can open **Backups** to create and list stored archives under `BACKUP_FOLDER` (default `instance/backups`). Each archive contains the SQLite database (or JSON dump) and all uploaded files.
- To restore, upload a backup zip from the same page; it overwrites the current SQLite database and `static/uploads/` contents.

## Project layout
```
app/
  __init__.py          # create_app factory, blueprint registration
  controllers/         # auth, core, admin, instructor, student, settings blueprints
  models/              # SQLAlchemy models
  services/            # timezone, exams (parsing/grading/export), migrations
  utils/               # auth helpers, uploads, template filters, parsing
templates/             # Jinja templates
static/                # CSS/assets/uploads
app.py                 # entrypoint (dev / initdb)
wsgi.py                # production WSGI entry
manage.py              # Flask CLI
```

## Running tests/checks
- Basic syntax check used during migration: `python3 -m compileall app manage.py wsgi.py`
- Add your own tests as needed; none are bundled.

## Deployment (Gunicorn example)
1. Install deps in a venv on the server.
2. Set environment:
   ```bash
   export SECRET_KEY="change-me"
   export DATABASE_URL="sqlite:///exam_app.db"  # or a Postgres URI
   export UPLOAD_FOLDER="/var/www/exam/static/uploads"
   ```
3. Initialize DB once:
   ```bash
   flask --app app initdb
   ```
4. Run with Gunicorn:
   ```bash
   gunicorn "wsgi:app" --bind 0.0.0.0:8000 --workers 4
   ```
5. Serve `static/` via your web server (or let Flask serve it if acceptable).

## Notes
- File uploads land in `UPLOAD_FOLDER`; ensure the directory is writable.
- Blueprints keep routes compatible with prior paths, but template `url_for` calls use namespaced endpoints (e.g., `auth.login`, `core.index`, `instructor.*`, `student.*`, `admin.*`, `settings.*`).
- For database upgrades beyond the built-in `migrate_schema`, consider adding Flask-Migrate/Alembic.***

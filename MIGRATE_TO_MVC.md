# MVC Migration Plan

Goal: split the current single-file Flask app (`app.py`) into a maintainable MVC layout with clear separation of concerns (configuration, models, controllers/blueprints, services/utils, templates, static assets) without changing runtime behavior.

## Target Layout
```
project-root/
├─ app/
│  ├─ __init__.py          # create_app factory, config registration, blueprint registration
│  ├─ config.py            # settings (database URI, secrets, upload paths, timezones)
│  ├─ extensions.py        # db, migrate, login manager-like helpers
│  ├─ models/
│  │  └─ __init__.py       # Tenant, User, Exam, Question, Choice, Attempt, Answer, ExamProgress, ExamDeletionLog, AccessLog
│  ├─ controllers/         # Blueprints
│  │  ├─ auth.py           # login, logout, login_required decorator
│  │  ├─ admin.py          # admin dashboard, user/tenant CRUD, logs
│  │  ├─ instructor.py     # exam CRUD, questions, answer keys, exports, close/delete
│  │  ├─ student.py        # dashboard, start exam, show/review/submit question flow
│  │  └─ settings.py       # timezone/password settings
│  ├─ services/            # pure logic helpers
│  │  ├─ timezone.py       # to_local, local_to_utc, fmt_dt, fmt_datetime_local_input, TIMEZONE_OPTIONS, is_rtl_text
│  │  ├─ exams.py          # create_questions, attempt_end_time, ensure_time_left, grade_attempt, export_exam_to_workbook
│  │  └─ migrations.py     # migrate_schema, ensure_default_tenant, ensure_column, init_db seeds
│  ├─ templates/           # move existing templates under blueprint folders if desired
│  ├─ static/              # current static assets + uploads
│  └─ utils/
│     └─ decorators.py     # login_required wrapper
├─ migrations/             # Flask-Migrate output (after init)
├─ wsgi.py                 # production entrypoint (create_app)
├─ manage.py               # optional CLI: `flask db upgrade`, `flask initdb`
├─ requirements.txt
└─ instance/               # instance config / sqlite db stays here
```

## Step-by-Step Migration
1. **Create application package**  
   - Make the `app/` package with `__init__.py`, `config.py`, `extensions.py`.  
   - Move `SECRET_KEY`, DB URI, upload folder, timezone defaults into `config.py` (read env vars).  
   - In `__init__.py`, build a `create_app()` that loads config, initializes extensions, registers blueprints, and calls `migrate_schema()` once at startup.

2. **Extract extensions**  
   - Move the global `db = SQLAlchemy()` into `extensions.py`.  
   - Import `db` from `extensions` everywhere models/controllers need it.

3. **Move models into `app/models/__init__.py`**  
   - Cut the model classes from `app.py` into this module without changing definitions.  
   - Keep relationships and helper methods (`User.check_password`, `Exam.is_active`, `Exam.has_answer_key`, `Attempt.as_order_list`).

4. **Extract services/helpers**  
   - `timezone.py`: `TIMEZONE_OPTIONS`, `to_local`, `local_to_utc`, `fmt_dt`, `fmt_datetime_local_input`, `is_rtl_text`.  
   - `exams.py`: `parse_questions_from_excel`, `create_questions`, `attempt_end_time`, `ensure_time_left`, `grade_attempt`, `export_exam_to_workbook`.  
   - `migrations.py`: `ensure_default_tenant`, `ensure_column`, `migrate_schema`, `init_db`.

5. **Create reusable decorators**  
   - Move `login_required` to `utils/decorators.py`; import it inside controllers to avoid circular imports.  
   - Add `inject_user` context processor to `app/__init__.py` or a dedicated `context.py`.

6. **Split routes into blueprints (controllers)**  
   - `auth` blueprint: `/login`, `/logout`, session handling.  
   - `admin` blueprint: admin dashboard, user CRUD, tenant CRUD, logs (`/admin`, `/logs`, `/admin/users/*`, `/admin/tenants/*`).  
   - `instructor` blueprint: dashboard, exam CRUD, question CRUD, answer key, export, close/delete.  
   - `student` blueprint: dashboard, start exam, show question, review, submit, view result.  
   - `settings` blueprint: timezone + password update.  
   - Preserve endpoint names (`endpoint="admin.dashboard"` etc.) and update `url_for` in templates accordingly.

7. **Restructure templates**  
   - Option A: keep current template filenames but move under `app/templates/`.  
   - Option B (clearer): group per blueprint, e.g. `templates/auth/login.html`, `templates/admin/dashboard.html`, `templates/instructor/question_form.html`, `templates/student/question.html`, etc.  
   - Update `render_template` calls to the new paths.

8. **Centralize static + uploads**  
   - Set `UPLOAD_FOLDER` in config; use `current_app.config["UPLOAD_FOLDER"]` instead of `app.root_path` concatenation.  
   - Keep `static/` for assets; ensure `uploads/` sits under `static/` or `instance/` and is created at startup.

9. **Add CLI entrypoints**  
   - Create `manage.py` or use Flask CLI commands in `create_app`.  
   - Register `flask initdb` command that wraps `init_db()` and `migrate_schema()`.

10. **Adopt migrations (optional but recommended)**  
    - Add `Flask-Migrate` to `requirements.txt`.  
    - Initialize with `flask db init`, then `flask db migrate` and `flask db upgrade`.  
    - Remove/limit raw `ALTER TABLE` calls once Alembic migrations cover them.

11. **Update imports and app runner**  
    - Replace `from app import db` style globals with `from app.extensions import db`.  
    - New `wsgi.py` should be:  
      ```python
      from app import create_app
      app = create_app()
      if __name__ == "__main__":
          app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
      ```
    - Delete or simplify the old `app.py` after verifying parity.

12. **Regression checklist**  
    - Verify login/logout + role checks.  
    - Instructor flows: create/edit exam, upload Excel, add/edit questions with images, answer keys, close/delete, export.  
    - Student flows: dashboard visibility per instructor, start exam, navigate questions, autosubmit on timeout, review/result.  
    - Admin flows: dashboard counts, user/tenant CRUD, logs.  
    - Timezone formatting and localization across dashboards.  
    - File upload path still works and is secured via `secure_filename`.

13. **Testing and cleanup**  
    - Run `flask initdb` (or existing `python wsgi.py initdb`) and smoke-test each role.  
    - Optionally add unit tests for services (`exams.grade_attempt`, `parse_questions_from_excel`, timezone helpers).  
    - Remove unused imports and dead code after the split.

Following these steps incrementally (create package → move models → move services → add blueprints → adjust templates → wire entrypoints) will convert the current monolithic `app.py` into a clean MVC structure while keeping behavior stable.

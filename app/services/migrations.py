import os

from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Tenant, User


def ensure_default_tenant():
    tenant = Tenant.query.filter_by(slug="default").first()
    if not tenant:
        tenant = Tenant(name="Default Tenant", slug="default")
        db.session.add(tenant)
        db.session.commit()
    return tenant


def ensure_column(table: str, column: str, ddl: str):
    inspector = inspect(db.engine)
    cols = [c["name"] for c in inspector.get_columns(table)]
    if column in cols:
        return False
    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    db.session.commit()
    return True


def migrate_schema():
    db.create_all()
    default_tenant = ensure_default_tenant()

    if "user" in inspect(db.engine).get_table_names():
        added = ensure_column("user", "tenant_id", "INTEGER")
        if added:
            db.session.execute(text("UPDATE user SET tenant_id = :tid WHERE tenant_id IS NULL"), {"tid": default_tenant.id})
        ensure_column("user", "instructor_id", "INTEGER")
        ensure_column("user", "timezone", "VARCHAR(64)")

    if "exam" in inspect(db.engine).get_table_names():
        added = ensure_column("exam", "tenant_id", "INTEGER")
        if added:
            db.session.execute(text("UPDATE exam SET tenant_id = :tid WHERE tenant_id IS NULL"), {"tid": default_tenant.id})
        if ensure_column("exam", "timezone", "VARCHAR(64)"):
            db.session.execute(text("UPDATE exam SET timezone = 'UTC' WHERE timezone IS NULL"))
        ensure_column("exam", "deleted_at", "DATETIME")
        ensure_column("exam", "question_limit", "INTEGER")
        ensure_column("exam", "is_closed", "BOOLEAN")
        ensure_column("exam", "closed_at", "DATETIME")

    if "question" in inspect(db.engine).get_table_names():
        added = ensure_column("question", "tenant_id", "INTEGER")
        if added:
            db.session.execute(text("UPDATE question SET tenant_id = :tid WHERE tenant_id IS NULL"), {"tid": default_tenant.id})
        ensure_column("question", "image_path", "VARCHAR(300)")
        ensure_column("question", "reason", "TEXT")
        ensure_column("question", "reason_image_path", "VARCHAR(300)")

    if "choice" in inspect(db.engine).get_table_names():
        added = ensure_column("choice", "tenant_id", "INTEGER")
        if added:
            db.session.execute(text("UPDATE choice SET tenant_id = :tid WHERE tenant_id IS NULL"), {"tid": default_tenant.id})
        ensure_column("choice", "image_path", "VARCHAR(300)")

    if "attempt" in inspect(db.engine).get_table_names():
        added = ensure_column("attempt", "tenant_id", "INTEGER")
        if added:
            db.session.execute(text("UPDATE attempt SET tenant_id = :tid WHERE tenant_id IS NULL"), {"tid": default_tenant.id})

    if "answer" in inspect(db.engine).get_table_names():
        added = ensure_column("answer", "tenant_id", "INTEGER")
        if added:
            db.session.execute(text("UPDATE answer SET tenant_id = :tid WHERE tenant_id IS NULL"), {"tid": default_tenant.id})

    db.create_all()
    db.session.commit()


def init_db():
    migrate_schema()
    tenant = ensure_default_tenant()
    if not User.query.filter_by(username="admin").first():
        admin_user = User(
            username="admin",
            full_name="Admin",
            role="admin",
            password_hash=generate_password_hash("admin123"),
            tenant_id=tenant.id,
        )
        db.session.add(admin_user)
    if not User.query.filter_by(username="instructor").first():
        instructor = User(
            username="instructor",
            full_name="Default Instructor",
            role="instructor",
            password_hash=generate_password_hash("instructor123"),
            tenant_id=tenant.id,
        )
        db.session.add(instructor)
    if not User.query.filter_by(username="student1").first():
        default_instructor = User.query.filter_by(username="instructor").first()
        student = User(
            username="student1",
            full_name="Student One",
            role="student",
            password_hash=generate_password_hash("student123"),
            tenant_id=tenant.id,
            instructor_id=default_instructor.id if default_instructor else None,
        )
        db.session.add(student)
    db.session.commit()
    print("Database initialized. Default logins: instructor/instructor123 and student1/student123.")

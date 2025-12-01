import json
from datetime import datetime, timezone

from werkzeug.security import check_password_hash

from app.extensions import db


class Tenant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # instructor or student
    full_name = db.Column(db.String(120))
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    tenant = db.relationship("Tenant")
    instructor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    instructor = db.relationship("User", remote_side=[id], backref="students", foreign_keys=[instructor_id])
    timezone = db.Column(db.String(64))

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    start_at = db.Column(db.DateTime, nullable=False)
    end_at = db.Column(db.DateTime, nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    tenant = db.relationship("Tenant")
    creator = db.relationship("User", foreign_keys=[created_by])
    timezone = db.Column(db.String(64), nullable=False, default="UTC")
    deleted_at = db.Column(db.DateTime)
    question_limit = db.Column(db.Integer)
    is_closed = db.Column(db.Boolean, default=False)
    closed_at = db.Column(db.DateTime)

    questions = db.relationship("Question", backref="exam", cascade="all, delete-orphan")

    def is_active(self, now: datetime) -> bool:
        start = self.start_at if self.start_at.tzinfo else self.start_at.replace(tzinfo=timezone.utc)
        end = self.end_at if self.end_at.tzinfo else self.end_at.replace(tzinfo=timezone.utc)
        current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return (not self.is_closed) and start <= current <= end

    def has_answer_key(self) -> bool:
        return bool(self.questions) and all(any(choice.is_correct for choice in q.choices) for q in self.questions)


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    qtype = db.Column(db.String(20), nullable=False)  # single or multiple
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    tenant = db.relationship("Tenant")
    image_path = db.Column(db.String(300))
    reason = db.Column(db.Text)
    reason_image_path = db.Column(db.String(300))

    choices = db.relationship("Choice", backref="question", cascade="all, delete-orphan", order_by="Choice.id")


class Choice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    text = db.Column(db.String(400), nullable=False)
    image_path = db.Column(db.String(300))
    is_correct = db.Column(db.Boolean, default=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    tenant = db.relationship("Tenant")


class Attempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime)
    score_percent = db.Column(db.Float)
    num_correct = db.Column(db.Integer)
    num_questions = db.Column(db.Integer)
    question_order = db.Column(db.Text, nullable=False)  # JSON list of question IDs
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    tenant = db.relationship("Tenant")
    student = db.relationship("User")

    exam = db.relationship("Exam", backref="attempts")

    def as_order_list(self) -> list[int]:
        try:
            return json.loads(self.question_order)
        except json.JSONDecodeError:
            return []


class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempt.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    choice_id = db.Column(db.Integer, db.ForeignKey("choice.id"), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)

    attempt = db.relationship("Attempt", backref="answers")
    choice = db.relationship("Choice")
    tenant = db.relationship("Tenant")


class ExamProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    asked_questions = db.Column(db.Text, default="[]")  # JSON list of question IDs already asked in cycle

    exam = db.relationship("Exam")
    student = db.relationship("User")
    tenant = db.relationship("Tenant")


class ExamDeletionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)
    exam_title = db.Column(db.String(200))
    instructor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    note = db.Column(db.Text)

    instructor = db.relationship("User", foreign_keys=[instructor_id])
    tenant = db.relationship("Tenant")


class AccessLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(64))
    path = db.Column(db.String(400))
    method = db.Column(db.String(10))
    user_agent = db.Column(db.String(400))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
    tenant = db.relationship("Tenant")

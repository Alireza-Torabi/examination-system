import json
import random
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import Answer, Attempt, Choice, Exam, ExamProgress, Question
from app.services.exams import attempt_end_time, ensure_time_left, grade_attempt
from app.services.timezone import TIMEZONE_OPTIONS, fmt_dt, to_local
from app.utils.auth import get_current_user, login_required

bp = Blueprint("student", __name__)


@bp.route("/student")
@login_required(role="student")
def student_dashboard():
    user = get_current_user()
    user_tz = user.timezone or "UTC"
    now_utc = datetime.now(timezone.utc)
    now_local = to_local(now_utc, user_tz)
    exams_query = Exam.query.filter_by(tenant_id=user.tenant_id).filter(Exam.deleted_at.is_(None))
    if user.instructor_id:
        exams_query = exams_query.filter_by(created_by=user.instructor_id)
    else:
        exams_query = exams_query.filter_by(created_by=None)  # no exams will match
    exams = exams_query.order_by(Exam.start_at.asc()).all()
    attempts = Attempt.query.filter_by(student_id=user.id).all()
    attempts_by_exam = {a.exam_id: a for a in attempts}
    exam_views = []
    for ex in exams:
        attempt = attempts_by_exam.get(ex.id)
        has_key = ex.has_answer_key()
        status = "blocked"
        can_start = False
        start_utc = ex.start_at if ex.start_at.tzinfo else ex.start_at.replace(tzinfo=timezone.utc)
        end_utc = ex.end_at if ex.end_at.tzinfo else ex.end_at.replace(tzinfo=timezone.utc)
        start_local = to_local(start_utc, user_tz)
        end_local = to_local(end_utc, user_tz)
        countdown_seconds = max(0, int((start_utc - now_utc).total_seconds()))
        if not has_key:
            status = "not_ready"
        elif now_utc < start_utc:
            status = "upcoming"
        elif ex.is_closed:
            status = "closed"
        else:
            if attempt and attempt.submitted_at:
                status = "completed_active"
            elif attempt and not attempt.submitted_at:
                status = "active"
            else:
                status = "active"
            can_start = True
        exam_views.append(
            {
                "exam": ex,
                "attempt": attempt,
                "status": status,
                "can_start": can_start,
                "countdown_seconds": countdown_seconds,
                "start_local": fmt_dt(start_local),
                "end_local": fmt_dt(end_local),
            }
        )
    return render_template(
        "student_dashboard.html",
        exam_views=exam_views,
        now=now_local,
        user_timezone=user_tz,
        attempts=attempts,
    )


@bp.route("/exam/<int:exam_id>/start")
@login_required(role="student")
def start_exam(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam:
        abort(404)
    user = get_current_user()
    if user.role != "admin":
        if not user.instructor_id or exam.created_by != user.instructor_id:
            abort(403)
    if exam.deleted_at:
        flash("This exam was deleted.")
        return redirect(url_for("student.student_dashboard"))
    now = datetime.now(timezone.utc)
    start_utc = exam.start_at if exam.start_at.tzinfo else exam.start_at.replace(tzinfo=timezone.utc)
    if now < start_utc:
        flash("This exam has not started yet.")
        return redirect(url_for("student.student_dashboard"))
    if exam.is_closed:
        flash("This exam has been closed by the instructor.")
        return redirect(url_for("student.student_dashboard"))
    if exam.tenant_id != user.tenant_id:
        abort(403)
    if not exam.has_answer_key():
        flash("This exam is not yet ready. Please try later.")
        return redirect(url_for("student.student_dashboard"))

    attempt = Attempt.query.filter_by(exam_id=exam.id, student_id=user.id, submitted_at=None).first()
    if attempt is None:
        progress = ExamProgress.query.filter_by(exam_id=exam.id, student_id=user.id).first()
        if not progress:
            progress = ExamProgress(
                exam_id=exam.id,
                student_id=user.id,
                tenant_id=exam.tenant_id,
                asked_questions="[]",
            )
            db.session.add(progress)
            db.session.commit()
        try:
            asked_set = set(json.loads(progress.asked_questions or "[]"))
        except json.JSONDecodeError:
            asked_set = set()
        all_qids = [q.id for q in exam.questions]
        if not all_qids:
            flash("No questions available for this exam.")
            return redirect(url_for("student.student_dashboard"))
        if len(asked_set) >= len(all_qids):
            asked_set = set()
        available = [qid for qid in all_qids if qid not in asked_set]
        if not available:
            asked_set = set()
            available = all_qids.copy()
        random.shuffle(available)
        if exam.question_limit and exam.question_limit > 0:
            selected = available[: exam.question_limit]
        else:
            selected = available
        if not selected:
            flash("No questions available to start the exam.")
            return redirect(url_for("student.student_dashboard"))
        asked_set.update(selected)
        progress.asked_questions = json.dumps(list(asked_set))
        db.session.add(progress)

        attempt = Attempt(
            exam=exam,
            student_id=user.id,
            started_at=datetime.utcnow(),
            question_order=json.dumps(selected),
            num_questions=len(selected),
            tenant_id=exam.tenant_id,
        )
        db.session.add(attempt)
        db.session.commit()
    return redirect(url_for("student.show_question", attempt_id=attempt.id, index=1))


def get_attempt_or_404(attempt_id: int) -> Attempt:
    attempt = db.session.get(Attempt, attempt_id)
    if not attempt:
        abort(404)
    user = get_current_user()
    if attempt.student_id != user.id or attempt.tenant_id != user.tenant_id:
        abort(403)
    return attempt


@bp.route("/attempt/<int:attempt_id>/question/<int:index>", methods=["GET", "POST"])
@login_required(role="student")
def show_question(attempt_id, index):
    attempt = get_attempt_or_404(attempt_id)
    if attempt.submitted_at:
        flash("Exam already submitted.")
        return redirect(url_for("student.view_result", attempt_id=attempt.id))

    order = attempt.as_order_list()
    if index < 1 or index > len(order):
        abort(404)
    question_id = order[index - 1]
    question = db.session.get(Question, question_id)
    if not question or question.exam_id != attempt.exam_id or question.tenant_id != attempt.tenant_id:
        abort(404)

    end_time = attempt_end_time(attempt)
    time_left_seconds = int((end_time - datetime.utcnow()).total_seconds())
    total_seconds = int((end_time - attempt.started_at).total_seconds())
    per_question_seconds = 0
    if len(order) > 0:
        per_question_seconds = max(1, total_seconds // len(order))
    if time_left_seconds <= 0:
        flash("Time is up. Auto-submitting your attempt with unanswered questions marked as empty.")
        grade_attempt(attempt)
        return redirect(url_for("student.view_result", attempt_id=attempt.id))

    existing_answers = Answer.query.filter_by(attempt_id=attempt.id, question_id=question.id).all()
    selected_ids = {ans.choice_id for ans in existing_answers}

    if request.method == "POST":
        if not ensure_time_left(attempt):
            flash("Time expired. Auto-submitting your attempt with unanswered questions marked as empty.")
            grade_attempt(attempt)
            return redirect(url_for("student.view_result", attempt_id=attempt.id))
        selected = request.form.getlist("choice")
        Answer.query.filter_by(attempt_id=attempt.id, question_id=question.id).delete()
        db.session.commit()
        for sid in selected:
            choice_obj = db.session.get(Choice, int(sid))
            if choice_obj and choice_obj.question_id == question.id:
                db.session.add(
                    Answer(
                        attempt=attempt,
                        question_id=question.id,
                        choice_id=choice_obj.id,
                        tenant_id=attempt.tenant_id,
                    )
                )
        db.session.commit()
        action = request.form.get("action", "next")
        if action == "previous" and index > 1:
            return redirect(url_for("student.show_question", attempt_id=attempt.id, index=index - 1))
        if action == "review":
            return redirect(url_for("student.review_attempt", attempt_id=attempt.id))
        next_index = index + 1
        if next_index > len(order):
            return redirect(url_for("student.review_attempt", attempt_id=attempt.id))
        return redirect(url_for("student.show_question", attempt_id=attempt.id, index=next_index))

    return render_template(
        "question.html",
        attempt=attempt,
        question=question,
        index=index,
        total=len(order),
        selected_ids=selected_ids,
        time_left_seconds=time_left_seconds,
        total_seconds=total_seconds,
        per_question_seconds=per_question_seconds,
    )


@bp.route("/attempt/<int:attempt_id>/review")
@login_required(role="student")
def review_attempt(attempt_id):
    attempt = get_attempt_or_404(attempt_id)
    order = attempt.as_order_list()
    questions = []
    for qid in order:
        q_obj = db.session.get(Question, qid)
        if q_obj and q_obj.tenant_id == attempt.tenant_id:
            questions.append(q_obj)
    answers_map = {}
    for ans in Answer.query.filter_by(attempt_id=attempt.id).all():
        answers_map.setdefault(ans.question_id, set()).add(ans.choice_id)
    return render_template(
        "review.html",
        attempt=attempt,
        questions=questions,
        answers_map=answers_map,
        time_left_seconds=max(0, int((attempt_end_time(attempt) - datetime.utcnow()).total_seconds())),
    )


@bp.route("/attempt/<int:attempt_id>/submit", methods=["POST"])
@login_required(role="student")
def submit_attempt(attempt_id):
    attempt = get_attempt_or_404(attempt_id)
    if attempt.submitted_at:
        return redirect(url_for("student.view_result", attempt_id=attempt.id))
    grade_attempt(attempt)
    flash("Exam submitted.")
    return redirect(url_for("student.view_result", attempt_id=attempt.id))


@bp.route("/attempt/<int:attempt_id>/result")
@login_required(role="student")
def view_result(attempt_id):
    attempt = get_attempt_or_404(attempt_id)
    if not attempt.submitted_at:
        flash("Please submit your exam first.")
        return redirect(url_for("student.review_attempt", attempt_id=attempt.id))
    order = attempt.as_order_list()
    questions = []
    for qid in order:
        q_obj = db.session.get(Question, qid)
        if q_obj and q_obj.tenant_id == attempt.tenant_id:
            questions.append(q_obj)
    answers_map = {}
    for ans in Answer.query.filter_by(attempt_id=attempt.id).all():
        answers_map.setdefault(ans.question_id, set()).add(ans.choice_id)
    return render_template("result.html", attempt=attempt, questions=questions, answers_map=answers_map)

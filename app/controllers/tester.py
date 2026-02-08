import json
import random
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from app.extensions import db
from app.models import Answer, Attempt, Choice, Exam, ExamProgress, Question, TesterReview
from app.services.exams import attempt_end_time, grade_attempt
from app.services.timezone import fmt_dt, to_local
from app.utils.auth import get_current_user, login_required

bp = Blueprint("tester", __name__)


def get_attempt_or_404(attempt_id: int) -> Attempt:
    attempt = db.session.get(Attempt, attempt_id)
    if not attempt:
        abort(404)
    user = get_current_user()
    if attempt.student_id != user.id or attempt.tenant_id != user.tenant_id:
        abort(403)
    return attempt


@bp.route("/tester")
@login_required(role="tester")
def tester_dashboard():
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
        status = "not_ready" if not has_key else "active"
        start_utc = ex.start_at if ex.start_at.tzinfo else ex.start_at.replace(tzinfo=timezone.utc)
        end_utc = ex.end_at if ex.end_at.tzinfo else ex.end_at.replace(tzinfo=timezone.utc)
        start_local = to_local(start_utc, user_tz)
        end_local = to_local(end_utc, user_tz)
        countdown_seconds = max(0, int((start_utc - now_utc).total_seconds()))
        exam_views.append(
            {
                "exam": ex,
                "attempt": attempt,
                "status": status,
                "raw_status": "active" if status == "active" else "not_ready",
                "can_start": status == "active",
                "countdown_seconds": countdown_seconds,
                "start_local": fmt_dt(start_local),
                "end_local": fmt_dt(end_local),
                "has_key": has_key,
            }
        )
    return render_template(
        "student_dashboard.html",
        exam_views=exam_views,
        now=now_local,
        user_timezone=user_tz,
        attempts=attempts,
        tester_mode=True,
    )


@bp.route("/tester/exam/<int:exam_id>/start")
@login_required(role="tester")
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
        return redirect(url_for("tester.tester_dashboard"))
    if exam.tenant_id != user.tenant_id:
        abort(403)
    if not exam.has_answer_key():
        flash("This exam is not yet ready. Please try later.")
        return redirect(url_for("tester.tester_dashboard"))

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
            return redirect(url_for("tester.tester_dashboard"))
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
            return redirect(url_for("tester.tester_dashboard"))
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
    return redirect(url_for("tester.show_question", attempt_id=attempt.id, index=1))


@bp.route("/tester/attempt/<int:attempt_id>/question/<int:index>", methods=["GET", "POST"])
@login_required(role="tester")
def show_question(attempt_id, index):
    attempt = get_attempt_or_404(attempt_id)
    if attempt.submitted_at:
        flash("Exam already submitted.")
        return redirect(url_for("tester.view_result", attempt_id=attempt.id))

    order = attempt.as_order_list()
    if index < 1 or index > len(order):
        abort(404)
    question_id = order[index - 1]
    question = db.session.get(Question, question_id)
    if not question or question.exam_id != attempt.exam_id or question.tenant_id != attempt.tenant_id:
        abort(404)

    # Tester has no time limit, but keep metrics for UI compatibility.
    end_time = attempt_end_time(attempt)
    time_left_seconds = int((end_time - datetime.utcnow()).total_seconds())
    total_seconds = int((end_time - attempt.started_at).total_seconds())
    per_question_seconds = 0
    if len(order) > 0:
        per_question_seconds = max(1, total_seconds // len(order))

    existing_answers = Answer.query.filter_by(attempt_id=attempt.id, question_id=question.id).all()
    selected_ids = {ans.choice_id for ans in existing_answers}
    correct_ids = {c.id for c in question.choices if c.is_correct}
    tester_feedback = None
    if selected_ids:
        tester_feedback = {
            "selected": selected_ids,
            "correct": correct_ids,
            "is_correct": bool(correct_ids) and selected_ids == correct_ids,
        }

    if request.method == "POST":
        selected = request.form.getlist("choice")
        Answer.query.filter_by(attempt_id=attempt.id, question_id=question.id).delete()
        db.session.commit()
        selected_ids = {int(sid) for sid in selected if sid.isdigit()}
        for sid in selected_ids:
            choice_obj = db.session.get(Choice, sid)
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
            return redirect(url_for("tester.show_question", attempt_id=attempt.id, index=index - 1))
        if action == "review":
            return redirect(url_for("tester.review_attempt", attempt_id=attempt.id))
        next_index = index + 1
        if next_index > len(order):
            return redirect(url_for("tester.review_attempt", attempt_id=attempt.id))
        return redirect(url_for("tester.show_question", attempt_id=attempt.id, index=next_index))

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
        tester_mode=True,
        tester_feedback=tester_feedback,
    )


@bp.route("/tester/attempt/<int:attempt_id>/review")
@login_required(role="tester")
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
        time_left_seconds=0,
        tester_mode=True,
    )


@bp.route("/tester/attempt/<int:attempt_id>/submit", methods=["POST"])
@login_required(role="tester")
def submit_attempt(attempt_id):
    attempt = get_attempt_or_404(attempt_id)
    if attempt.submitted_at:
        return redirect(url_for("tester.view_result", attempt_id=attempt.id))
    grade_attempt(attempt)
    flash("Exam submitted.")
    return redirect(url_for("tester.view_result", attempt_id=attempt.id))


@bp.route("/tester/attempt/<int:attempt_id>/result")
@login_required(role="tester")
def view_result(attempt_id):
    attempt = get_attempt_or_404(attempt_id)
    if not attempt.submitted_at:
        flash("Please submit your exam first.")
        return redirect(url_for("tester.review_attempt", attempt_id=attempt.id))
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
        "result.html",
        attempt=attempt,
        questions=questions,
        answers_map=answers_map,
        tester_mode=True,
    )


@bp.route("/tester/attempt/<int:attempt_id>/question/<int:question_id>/instant", methods=["POST"])
@login_required(role="tester")
def tester_instant_feedback(attempt_id, question_id):
    attempt = get_attempt_or_404(attempt_id)
    question = db.session.get(Question, question_id)
    if not question or question.exam_id != attempt.exam_id or question.tenant_id != attempt.tenant_id:
        abort(404)
    selected_ids = {int(v) for v in request.form.getlist("choice") if v.isdigit()}
    if question.qtype == "single" and selected_ids:
        first = next(iter(selected_ids))
        selected_ids = {first}
    Answer.query.filter_by(attempt_id=attempt.id, question_id=question.id).delete()
    for sid in selected_ids:
        choice_obj = db.session.get(Choice, sid)
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
    correct_ids = {c.id for c in question.choices if c.is_correct}
    is_correct = bool(correct_ids) and selected_ids == correct_ids
    return jsonify(
        {
            "correct_ids": list(correct_ids),
            "selected_ids": list(selected_ids),
            "is_correct": is_correct,
            "reason_html": question.reason or "",
            "reason_image_url": url_for("static", filename=question.reason_image_path)
            if question.reason_image_path
            else "",
        }
    )


@bp.route("/tester/questions/<int:question_id>/reason", methods=["POST"])
@login_required(role="tester")
def update_reason(question_id):
    question = db.session.get(Question, question_id)
    if not question or question.exam.deleted_at:
        abort(404)
    user = get_current_user()
    exam = question.exam
    if question.tenant_id != user.tenant_id or not user.instructor_id or exam.created_by != user.instructor_id:
        abort(403)
    reason_text = request.form.get("reason", "")
    cleaned_reason = reason_text.strip() if reason_text is not None else ""
    question.reason = cleaned_reason or None
    db.session.commit()
    flash("Reason updated.")
    attempt_id = request.form.get("attempt_id", type=int)
    index = request.form.get("index", type=int) or 1
    if attempt_id:
        return redirect(url_for("tester.show_question", attempt_id=attempt_id, index=index))
    return redirect(url_for("tester.tester_dashboard"))


@bp.route("/tester/questions/<int:question_id>/review", methods=["POST"])
@login_required(role="tester")
def request_review(question_id):
    question = db.session.get(Question, question_id)
    if not question or question.exam.deleted_at:
        abort(404)
    user = get_current_user()
    exam = question.exam
    if question.tenant_id != user.tenant_id or not user.instructor_id or exam.created_by != user.instructor_id:
        abort(403)
    proposed = {int(cid) for cid in request.form.getlist("proposed_choice") if cid.isdigit()}
    proposed_reason = request.form.get("proposed_reason", "").strip()
    note = request.form.get("note", "").strip()
    if not proposed:
        flash("Select at least one option for the proposed correct answer.")
        attempt_id = request.form.get("attempt_id", type=int)
        index = request.form.get("index", type=int) or 1
        if attempt_id:
            return redirect(url_for("tester.show_question", attempt_id=attempt_id, index=index))
        return redirect(url_for("tester.tester_dashboard"))
    review = TesterReview(
        exam_id=exam.id,
        question_id=question.id,
        tester_id=user.id,
        tenant_id=question.tenant_id,
        proposed_choice_ids=json.dumps(sorted(list(proposed))),
        proposed_reason=proposed_reason or None,
        note=note or None,
    )
    db.session.add(review)
    db.session.commit()
    flash("Review request sent to instructor.")
    attempt_id = request.form.get("attempt_id", type=int)
    index = request.form.get("index", type=int) or 1
    if attempt_id:
        return redirect(url_for("tester.show_question", attempt_id=attempt_id, index=index))
    return redirect(url_for("tester.tester_dashboard"))

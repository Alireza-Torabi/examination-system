import io
import random
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from app.extensions import db
from app.models import Answer, Attempt, Choice, Exam, ExamDeletionLog, ExamProgress, Question, Tenant, User
from app.services.exams import (
    create_questions,
    export_exam_to_workbook,
    parse_questions_from_excel,
)
from app.services.timezone import TIMEZONE_OPTIONS, fmt_dt, fmt_datetime_local_input, local_to_utc, to_local
from app.utils.auth import get_current_user, login_required
from app.utils.helpers import ALLOWED_IMAGE_EXTENSIONS, parse_datetime, save_image_file

bp = Blueprint("instructor", __name__)


@bp.route("/instructor")
@login_required(role="instructor")
def instructor_dashboard():
    user = get_current_user()
    if user.role == "admin":
        return redirect(url_for("admin.admin_dashboard"))
    user_tz = user.timezone or "UTC"
    now_utc = datetime.now(timezone.utc)
    now_local = to_local(now_utc, user_tz)
    exams_raw = (
        Exam.query.filter_by(tenant_id=user.tenant_id, created_by=user.id)
        .filter(Exam.deleted_at.is_(None))
        .order_by(Exam.start_at.desc())
        .all()
    )
    exams = []
    for ex in exams_raw:
        start_local = fmt_dt(to_local(ex.start_at if ex.start_at.tzinfo else ex.start_at.replace(tzinfo=timezone.utc), user_tz))
        end_local = fmt_dt(to_local(ex.end_at if ex.end_at.tzinfo else ex.end_at.replace(tzinfo=timezone.utc), user_tz))
        exams.append({"obj": ex, "start_local": start_local, "end_local": end_local})
    return render_template("instructor_dashboard.html", exams=exams, now=now_local, user_timezone=user_tz)


@bp.route("/excel-template")
@login_required(role="instructor")
def excel_template():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Questions"
    ws.append(
        [
            "Question",
            "QuestionImage",
            "Type (single/multiple)",
            "Option1",
            "Option1Image",
            "Option2",
            "Option2Image",
            "Option3",
            "Option3Image",
            "Option4",
            "Option4Image",
            "Option5",
            "Option5Image",
            "Option6",
            "Option6Image",
            "Correct (letters, e.g. A or A,C)",
            "Reason (optional)",
        ]
    )
    ws.append(
        [
            "What is 2+2?",
            "",
            "single",
            "2",
            "",
            "3",
            "",
            "4",
            "",
            "5",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "C",
            "Basic arithmetic.",
        ]
    )
    ws.append(
        [
            "Select prime numbers",
            "",
            "multiple",
            "2",
            "",
            "3",
            "",
            "4",
            "",
            "9",
            "",
            "11",
            "",
            "15",
            "",
            "",
            "",
            "A,E",
            "2 and 11 are prime.",
        ]
    )
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="exam_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/instructor/uploads/images", methods=["POST"])
@login_required(role=["instructor", "admin"])
def upload_rte_image():
    image_file = request.files.get("file")
    if not image_file or not image_file.filename:
        return jsonify({"error": "No file provided"}), 400
    try:
        image_path = save_image_file(image_file)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not image_path:
        return jsonify({"error": "Invalid file"}), 400
    return jsonify({"location": url_for("static", filename=image_path)})


@bp.route("/instructor/exams/new", methods=["GET", "POST"])
@login_required(role="instructor")
def create_exam():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "")
        start_at_raw = parse_datetime(request.form.get("start_at", ""))
        end_at_raw = parse_datetime(request.form.get("end_at", ""))
        duration_minutes = request.form.get("duration_minutes", "").strip()
        timezone = request.form.get("timezone", "UTC").strip() or "UTC"
        question_limit = request.form.get("question_limit", "").strip()
        upload = request.files.get("questions_file")

        if not title or not start_at_raw or not end_at_raw or not duration_minutes:
            flash("Title, times, and duration are required.")
            return redirect(request.url)
        try:
            duration_minutes = int(duration_minutes)
        except ValueError:
            flash("Duration must be a number of minutes.")
            return redirect(request.url)
        if question_limit:
            try:
                question_limit = int(question_limit)
                if question_limit <= 0:
                    raise ValueError()
            except ValueError:
                flash("Question count must be a positive number.")
                return redirect(request.url)
        else:
            question_limit = None
        start_at = local_to_utc(start_at_raw, timezone)
        end_at = local_to_utc(end_at_raw, timezone)
        if end_at <= start_at:
            flash("End time must be after start time.")
            return redirect(request.url)

        question_defs = []
        if upload and upload.filename:
            try:
                question_defs = parse_questions_from_excel(upload)
            except Exception as exc:  # pylint: disable=broad-except
                flash(str(exc))
                return redirect(request.url)

        user = get_current_user()
        exam = Exam(
            title=title,
            description=description,
            start_at=start_at,
            end_at=end_at,
            duration_minutes=duration_minutes,
            created_by=user.id,
            tenant_id=user.tenant_id,
            timezone=timezone,
            question_limit=question_limit,
        )
        db.session.add(exam)
        db.session.flush()
        if question_defs:
            create_questions(exam, question_defs)
        db.session.commit()
        if question_defs:
            flash("Exam created. Please set the correct answers.")
            return redirect(url_for("instructor.answer_key", exam_id=exam.id))
        flash("Exam created. Add questions from the UI.")
        return redirect(url_for("instructor.add_question", exam_id=exam.id))

    return render_template("exam_form.html", timezone_options=TIMEZONE_OPTIONS)


@bp.route("/instructor/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@login_required(role=["instructor", "admin"])
def edit_exam(exam_id):
    user = get_current_user()
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.deleted_at:
        abort(404)
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(403)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "")
        start_at_raw = parse_datetime(request.form.get("start_at", ""))
        end_at_raw = parse_datetime(request.form.get("end_at", ""))
        duration_minutes = request.form.get("duration_minutes", "").strip()
        timezone = request.form.get("timezone", exam.timezone or "UTC").strip() or "UTC"
        question_limit = request.form.get("question_limit", "").strip()
        if not title or not start_at_raw or not end_at_raw or not duration_minutes:
            flash("Title, times, and duration are required.")
            return redirect(request.url)
        try:
            duration_minutes = int(duration_minutes)
        except ValueError:
            flash("Duration must be a number of minutes.")
            return redirect(request.url)
        if question_limit:
            try:
                question_limit = int(question_limit)
                if question_limit <= 0:
                    raise ValueError()
            except ValueError:
                flash("Question count must be a positive number.")
                return redirect(request.url)
        else:
            question_limit = None
        start_at = local_to_utc(start_at_raw, timezone)
        end_at = local_to_utc(end_at_raw, timezone)
        if end_at <= start_at:
            flash("End time must be after start time.")
            return redirect(request.url)

        exam.title = title
        exam.description = description
        exam.start_at = start_at
        exam.end_at = end_at
        exam.duration_minutes = duration_minutes
        exam.timezone = timezone
        exam.question_limit = question_limit
        db.session.commit()
        flash("Exam updated.")
        return redirect(url_for("instructor.instructor_dashboard"))

    start_val = fmt_datetime_local_input(exam.start_at, exam.timezone or "UTC")
    end_val = fmt_datetime_local_input(exam.end_at, exam.timezone or "UTC")
    return render_template(
        "exam_edit.html",
        exam=exam,
        start_val=start_val,
        end_val=end_val,
        timezone_options=TIMEZONE_OPTIONS,
    )


@bp.route("/instructor/exams/<int:exam_id>/answers", methods=["GET", "POST"])
@login_required(role="instructor")
def answer_key(exam_id):
    exam = db.session.get(Exam, exam_id)
    user = get_current_user()
    if not exam:
        abort(404)
    if exam.deleted_at:
        flash("This exam was deleted.")
        return redirect(url_for("instructor.instructor_dashboard"))
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(404)
    if request.method == "POST":
        for question in exam.questions:
            selected_ids = request.form.getlist(f"q_{question.id}")
            if question.qtype == "single" and len(selected_ids) > 1:
                selected_ids = selected_ids[:1]
            selected_ids_set = {int(sid) for sid in selected_ids}
            for choice in question.choices:
                choice.is_correct = choice.id in selected_ids_set
        db.session.commit()
        flash("Answer key saved.")
        return redirect(url_for("instructor.answer_key", exam_id=exam.id))
    return render_template("answer_key.html", exam=exam)


@bp.route("/instructor/exams/<int:exam_id>/questions/new", methods=["GET", "POST"])
@login_required(role="instructor")
def add_question(exam_id):
    user = get_current_user()
    exam = db.session.get(Exam, exam_id)
    if not exam:
        abort(404)
    if exam.deleted_at:
        flash("This exam was deleted.")
        return redirect(url_for("instructor.instructor_dashboard"))
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(404)
    if request.method == "POST":
        delete_one_id = request.form.get("delete_one")
        if delete_one_id:
            try:
                qid = int(delete_one_id)
            except ValueError:
                flash("Invalid question selected.")
                return redirect(request.url)
            question_to_delete = Question.query.filter_by(id=qid, exam_id=exam.id).first()
            if not question_to_delete:
                flash("Question not found or already deleted.")
                return redirect(request.url)
            Answer.query.filter_by(question_id=question_to_delete.id).delete()
            db.session.delete(question_to_delete)
            db.session.commit()
            flash("Question deleted.")
            return redirect(request.url)
        action = request.form.get("action", "add_more")
        if action == "delete_selected":
            selected_ids = [int(v) for v in request.form.getlist("selected_question") if v.isdigit()]
            if not selected_ids:
                flash("Please select at least one question to delete.")
                return redirect(request.url)
            questions_to_delete = (
                Question.query.filter(Question.exam_id == exam.id, Question.id.in_(selected_ids)).all()
            )
            if not questions_to_delete:
                flash("No matching questions were selected.")
                return redirect(request.url)
            for q in questions_to_delete:
                Answer.query.filter_by(question_id=q.id).delete()
                db.session.delete(q)
            db.session.commit()
            flash(f"Deleted {len(questions_to_delete)} question(s).")
            return redirect(request.url)
        if action == "import_excel":
            upload = request.files.get("questions_file")
            if not upload or not upload.filename:
                flash("Please choose an Excel file to upload.")
                return redirect(request.url)
            start_raw = request.form.get("import_start", "").strip()
            end_raw = request.form.get("import_end", "").strip()
            try:
                question_defs = parse_questions_from_excel(upload)
            except Exception as exc:  # pylint: disable=broad-except
                flash(str(exc))
                return redirect(request.url)
            if not question_defs:
                flash("No questions were found in the uploaded file.")
                return redirect(request.url)
            start_idx = 1
            end_idx = len(question_defs)
            try:
                if start_raw:
                    start_idx = int(start_raw)
                if end_raw:
                    end_idx = int(end_raw)
            except ValueError:
                flash("Please enter valid numbers for the question range.")
                return redirect(request.url)
            if start_idx < 1 or end_idx < 1 or start_idx > end_idx:
                flash("Invalid range. 'From' must be >= 1 and <= 'To'.")
                return redirect(request.url)
            if start_idx > len(question_defs):
                flash("Range start is beyond the available questions in the file.")
                return redirect(request.url)
            end_idx = min(end_idx, len(question_defs))
            selected_defs = question_defs[start_idx - 1 : end_idx]
            if not selected_defs:
                flash("No questions selected from the provided range.")
                return redirect(request.url)
            create_questions(exam, selected_defs)
            db.session.commit()
            flash(f"Added {len(selected_defs)} question(s) from Excel (rows {start_idx} to {end_idx}).")
            return redirect(request.url)
        text = request.form.get("text", "").strip()
        qtype = request.form.get("qtype", "single")
        qtype = "multiple" if qtype == "multiple" else "single"
        image_file = request.files.get("image")
        reason_image_file = request.files.get("reason_image")
        reason = request.form.get("reason", "").strip()
        option_fields = []
        for idx in range(1, 7):
            val = request.form.get(f"option{idx}", "").strip()
            opt_image = request.files.get(f"option_image{idx}")
            if val:
                option_fields.append((idx, val, opt_image))
            elif opt_image and opt_image.filename:
                flash("Please add text for any option that has an image.")
                return redirect(request.url)
        if not text:
            flash("Question text is required.")
            return redirect(request.url)
        if len(option_fields) < 2:
            flash("At least two options are required.")
            return redirect(request.url)
        correct_raw = {int(v) for v in request.form.getlist("correct") if v.isdigit()}
        options = []
        correct_indices = set()
        for idx, (field_idx, val, opt_image) in enumerate(option_fields):
            options.append({"text": val, "file": opt_image})
            if field_idx in correct_raw:
                correct_indices.add(idx)
        if not correct_indices:
            flash("Please select at least one correct answer.")
            return redirect(request.url)
        if qtype == "single":
            first = sorted(correct_indices)[0]
            correct_indices = {first}
        try:
            image_path = save_image_file(image_file)
            reason_image_path = save_image_file(reason_image_file)
        except ValueError as exc:
            flash(str(exc))
            return redirect(request.url)
        choices_payload = []
        for idx, opt in enumerate(options):
            try:
                opt_image_path = save_image_file(opt.get("file"))
            except ValueError as exc:
                flash(str(exc))
                return redirect(request.url)
            choices_payload.append(
                {"text": opt["text"], "image_path": opt_image_path, "is_correct": idx in correct_indices}
            )
        question = Question(
            exam=exam,
            text=text,
            qtype=qtype,
            tenant_id=exam.tenant_id,
            image_path=image_path,
            reason=reason or None,
            reason_image_path=reason_image_path,
        )
        db.session.add(question)
        db.session.flush()
        for payload in choices_payload:
            db.session.add(
                Choice(
                    question=question,
                    text=payload["text"],
                    image_path=payload["image_path"],
                    is_correct=payload["is_correct"],
                    tenant_id=exam.tenant_id,
                )
            )
        db.session.commit()
        flash("Question added.")
        if action == "finish":
            return redirect(url_for("instructor.answer_key", exam_id=exam.id))
        return redirect(request.url)
    return render_template("question_form.html", exam=exam)


@bp.route("/instructor/questions/<int:question_id>/edit", methods=["GET", "POST"])
@login_required(role=["instructor", "admin"])
def edit_question(question_id):
    user = get_current_user()
    question = db.session.get(Question, question_id)
    if not question or question.exam.deleted_at:
        abort(404)
    exam = question.exam
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(404)
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        qtype = request.form.get("qtype", "single")
        qtype = "multiple" if qtype == "multiple" else "single"
        image_file = request.files.get("image")
        remove_image = request.form.get("remove_image") == "on"
        reason_image_file = request.files.get("reason_image")
        remove_reason_image = request.form.get("remove_reason_image") == "on"
        reason = request.form.get("reason", "").strip()
        existing_choices = list(question.choices)
        existing_choice_images = {idx + 1: choice.image_path for idx, choice in enumerate(existing_choices)}
        option_fields = []
        for idx in range(1, 7):
            val = request.form.get(f"option{idx}", "").strip()
            opt_image = request.files.get(f"option_image{idx}")
            remove_opt_image = request.form.get(f"remove_option_image{idx}") == "on"
            if val:
                option_fields.append((idx, val, opt_image, remove_opt_image))
            elif opt_image and opt_image.filename:
                flash("Please add text for any option that has an image.")
                return redirect(request.url)
        if not text:
            flash("Question text is required.")
            return redirect(request.url)
        if len(option_fields) < 2:
            flash("At least two options are required.")
            return redirect(request.url)
        correct_raw = {int(v) for v in request.form.getlist("correct") if v.isdigit()}
        options = []
        correct_indices = set()
        for idx, (field_idx, val, opt_image, remove_opt_image) in enumerate(option_fields):
            options.append({"text": val, "file": opt_image, "remove_image": remove_opt_image, "field_idx": field_idx})
            if field_idx in correct_raw:
                correct_indices.add(idx)
        if not correct_indices:
            flash("Please select at least one correct answer.")
            return redirect(request.url)
        if qtype == "single":
            first = sorted(correct_indices)[0]
            correct_indices = {first}

        image_path = question.image_path
        if remove_image:
            image_path = None
        else:
            try:
                new_image_path = save_image_file(image_file)
            except ValueError as exc:
                flash(str(exc))
                return redirect(request.url)
            if new_image_path:
                image_path = new_image_path
        reason_image_path = question.reason_image_path
        if remove_reason_image:
            reason_image_path = None
        else:
            try:
                new_reason_image = save_image_file(reason_image_file)
            except ValueError as exc:
                flash(str(exc))
                return redirect(request.url)
            if new_reason_image:
                reason_image_path = new_reason_image

        choices_payload = []
        for idx, opt in enumerate(options):
            image_for_choice = existing_choice_images.get(opt["field_idx"])
            if opt.get("remove_image"):
                image_for_choice = None
            try:
                new_choice_image = save_image_file(opt.get("file"))
            except ValueError as exc:
                flash(str(exc))
                return redirect(request.url)
            if new_choice_image:
                image_for_choice = new_choice_image
            choices_payload.append(
                {"text": opt["text"], "image_path": image_for_choice, "is_correct": idx in correct_indices}
            )

        # Replace choices and answers
        Answer.query.filter_by(question_id=question.id).delete(synchronize_session=False)
        Choice.query.filter_by(question_id=question.id).delete(synchronize_session=False)
        question.choices = []
        db.session.flush()

        question.text = text
        question.qtype = qtype
        question.image_path = image_path
        question.reason = reason or None
        question.reason_image_path = reason_image_path
        db.session.add(question)
        db.session.flush()
        for payload in choices_payload:
            db.session.add(
                Choice(
                    question=question,
                    text=payload["text"],
                    image_path=payload["image_path"],
                    is_correct=payload["is_correct"],
                    tenant_id=exam.tenant_id,
                )
            )
        db.session.commit()
        flash("Question updated.")
        return redirect(url_for("instructor.add_question", exam_id=exam.id))

    options = question.choices
    letter_map = ["A", "B", "C", "D", "E", "F"]
    correct_indices = [idx for idx, c in enumerate(options) if c.is_correct]
    return render_template(
        "question_edit.html",
        question=question,
        exam=exam,
        options=options,
        correct_indices=correct_indices,
        letter_map=letter_map,
    )


@bp.route("/instructor/questions/<int:question_id>/preview")
@login_required(role=["instructor", "admin"])
def preview_question(question_id):
    question = db.session.get(Question, question_id)
    if not question or question.exam.deleted_at:
        abort(404)
    user = get_current_user()
    exam = question.exam
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(404)

    class Dummy:
        pass

    dummy_attempt = Dummy()
    dummy_attempt.exam = exam
    dummy_attempt.id = 0
    is_partial = request.args.get("partial") == "1"
    template_name = "question_preview_partial.html" if is_partial else "question_preview.html"
    return render_template(
        template_name,
        question=question,
        attempt=dummy_attempt,
        index=1,
        total=1,
        selected_ids=set(),
        time_left_seconds=0,
        total_seconds=0,
        per_question_seconds=0,
        is_preview=True,
    )


@bp.route("/instructor/exams/<int:exam_id>/delete", methods=["POST"])
@login_required(role=["instructor", "admin"])
def delete_exam(exam_id):
    user = get_current_user()
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.deleted_at:
        flash("Exam not found or already deleted.")
        return redirect(url_for("instructor.instructor_dashboard"))
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(404)
    exam.deleted_at = datetime.utcnow()
    db.session.add(
        ExamDeletionLog(
            exam_id=exam.id,
            exam_title=exam.title,
            instructor_id=user.id,
            tenant_id=exam.tenant_id,
            note="Deleted by instructor",
        )
    )
    db.session.commit()
    flash("Exam deleted and logged for admin review.")
    return redirect(url_for("instructor.instructor_dashboard"))


@bp.route("/instructor/exams/<int:exam_id>/toggle_close", methods=["POST"])
@login_required(role="instructor")
def toggle_close_exam(exam_id):
    user = get_current_user()
    exam = db.session.get(Exam, exam_id)
    if not exam:
        abort(404)
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(404)
    if exam.deleted_at:
        flash("Exam was deleted.")
        return redirect(url_for("instructor.instructor_dashboard"))
    exam.is_closed = not exam.is_closed
    exam.closed_at = datetime.utcnow() if exam.is_closed else None
    db.session.commit()
    flash("Exam closed." if exam.is_closed else "Exam reopened.")
    return redirect(url_for("instructor.instructor_dashboard"))


@bp.route("/instructor/exams/<int:exam_id>/results")
@login_required(role=["instructor", "admin"])
def instructor_exam_results(exam_id):
    user = get_current_user()
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.deleted_at:
        abort(404)
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(403)
    user_tz = user.timezone or "UTC"
    attempts_raw = Attempt.query.filter_by(exam_id=exam.id).order_by(Attempt.started_at.desc()).all()
    attempts = []
    for att in attempts_raw:
        attempts.append(
            {
                "obj": att,
                "student": att.student,
                "started_local": fmt_dt(to_local(att.started_at, user_tz)),
                "submitted_local": fmt_dt(to_local(att.submitted_at, user_tz)),
            }
        )
    return render_template(
        "exam_results.html",
        exam=exam,
        attempts=attempts,
        user_timezone=user_tz,
    )


@bp.route("/instructor/exams/<int:exam_id>/export")
@login_required(role=["instructor", "admin"])
def export_exam(exam_id):
    user = get_current_user()
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.deleted_at:
        abort(404)
    if user.role != "admin" and (exam.created_by != user.id or exam.tenant_id != user.tenant_id):
        abort(403)
    wb = export_exam_to_workbook(exam)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"exam_{exam.id}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

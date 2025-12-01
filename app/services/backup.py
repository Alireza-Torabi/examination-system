import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from flask import current_app
from sqlalchemy import Table, inspect, select

from app.extensions import db
from app.services.migrations import init_db


class BackupError(Exception):
    """Raised when backup generation or restore fails."""


def backup_folder() -> Path:
    """Return (and ensure) the folder used to store backups."""
    folder = Path(current_app.config["BACKUP_FOLDER"])
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sqlite_database_path() -> Path | None:
    """Resolve the SQLite database path if the configured engine uses SQLite."""
    url = db.engine.url
    if url.get_backend_name() != "sqlite":
        return None
    db_name = url.database
    if not db_name or db_name == ":memory:":
        return None
    path = Path(db_name)
    if not path.is_absolute():
        path = Path(current_app.instance_path) / path
    return path


def _copy_sqlite_db(source: Path) -> Path:
    """Create a consistent copy of the SQLite database using the backup API."""
    import sqlite3

    fd, dest_name = tempfile.mkstemp(prefix="exam_db_copy_", suffix=".db")
    os.close(fd)
    dest = Path(dest_name)
    with sqlite3.connect(source) as src_conn, sqlite3.connect(dest) as dest_conn:
        src_conn.backup(dest_conn)
    return dest


def _export_tables_as_json() -> dict[str, list[dict[str, Any]]]:
    """Fallback export for non-SQLite engines."""
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    dump: dict[str, list[dict[str, Any]]] = {}
    metadata = db.Model.metadata
    with db.engine.connect() as conn:
        for table_name in tables:
            table = Table(table_name, metadata, autoload_with=db.engine)
            result = conn.execute(select(table))
            dump[table_name] = [dict(row._mapping) for row in result]
    return dump


def _add_uploads(zip_file: zipfile.ZipFile) -> int:
    """Add uploaded files to the archive, returning the count."""
    uploads_dir = Path(current_app.config["UPLOAD_FOLDER"])
    if not uploads_dir.exists():
        return 0
    count = 0
    for file_path in uploads_dir.rglob("*"):
        if not file_path.is_file():
            continue
        arcname = Path("uploads") / file_path.relative_to(uploads_dir)
        zip_file.write(file_path, arcname=str(arcname))
        count += 1
    return count


def create_backup_archive(persist: bool = True) -> tuple[Path, str]:
    """Bundle the database and uploaded files into a zip archive."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    download_name = f"exam_backup_{timestamp}.zip"
    if persist:
        archive_path = backup_folder() / download_name
        archive_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fd, archive_name = tempfile.mkstemp(prefix="exam_backup_", suffix=".zip")
        os.close(fd)
        archive_path = Path(archive_name)

    manifest: dict[str, Any] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "db_backend": db.engine.url.get_backend_name(),
        "db_uri_masked": db.engine.url.render_as_string(hide_password=True),
        "upload_folder": current_app.config.get("UPLOAD_FOLDER"),
        "persisted": persist,
    }

    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            sqlite_path = _sqlite_database_path()
            dumped = False
            if sqlite_path and sqlite_path.exists():
                try:
                    copy_path = _copy_sqlite_db(sqlite_path)
                    try:
                        arcname = f"db/{sqlite_path.name}"
                        zf.write(copy_path, arcname=arcname)
                        manifest["database"] = {"mode": "sqlite-file", "path": arcname}
                        dumped = True
                    finally:
                        copy_path.unlink(missing_ok=True)
                except Exception as exc:  # pylint: disable=broad-except
                    current_app.logger.warning("SQLite backup failed, falling back to JSON dump: %s", exc)
            if not dumped:
                dump = _export_tables_as_json()
                arcname = "db/data.json"
                zf.writestr(arcname, json.dumps(dump, default=str, indent=2))
                manifest["database"] = {"mode": "table-json", "path": arcname}

            uploads_count = _add_uploads(zf)
            manifest["uploads"] = {
                "included": uploads_count > 0,
                "count": uploads_count,
                "relative_root": "uploads",
            }
            manifest_text = json.dumps(manifest, indent=2)
            zf.writestr("manifest.json", manifest_text)
    except Exception as exc:  # pylint: disable=broad-except
        archive_path.unlink(missing_ok=True)
        raise BackupError(f"Failed to create backup archive: {exc}") from exc

    return archive_path, download_name


def list_backups() -> list[dict[str, Any]]:
    """List stored backup files with basic metadata."""
    folder = backup_folder()
    backups: list[dict[str, Any]] = []
    for file_path in folder.glob("*.zip"):
        try:
            stat = file_path.stat()
        except OSError:
            continue
        created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        backups.append(
            {"name": file_path.name, "path": file_path, "size": stat.st_size, "created_at": created_at}
        )
    backups.sort(key=lambda item: item["created_at"], reverse=True)
    return backups


def _restore_sqlite_from_zip(zip_file: zipfile.ZipFile, work_dir: Path) -> str:
    """Restore the SQLite database from the zip archive."""
    target_db = _sqlite_database_path()
    if not target_db:
        raise BackupError("Restore is only supported for SQLite configurations.")
    entries = [n for n in zip_file.namelist() if n.lower().endswith(".db")]
    if not entries:
        raise BackupError("No SQLite database found in the backup archive.")

    # Always normalize DB extraction to work_dir/db/<filename> to avoid path traversal issues.
    raw_entry = entries[0]
    cleaned = raw_entry.replace("\\", "/")
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[1]
    filename = Path(cleaned).name
    if filename in ("", ".", ".."):
        raise BackupError("Backup archive contains unsafe paths.")
    dest_path = (work_dir / "db" / filename).resolve()
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with zip_file.open(raw_entry, "r") as src, open(dest_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    if not dest_path.exists():
        raise BackupError("Backup archive is missing the database file.")

    db.session.remove()
    db.engine.dispose()
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dest_path, target_db)
    return target_db.name


def _restore_uploads_from_zip(zip_file: zipfile.ZipFile) -> int:
    """Restore upload files from the archive."""
    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    uploads_root.mkdir(parents=True, exist_ok=True)
    restored = 0
    for info in zip_file.infolist():
        raw_name = info.filename
        safe_name = raw_name.replace("\\", "/")
        if ":" in safe_name:
            safe_name = safe_name.split(":", 1)[1]
        safe_name = safe_name.lstrip("/")
        parts = list(PurePosixPath(safe_name).parts)
        # Accept uploads/... or static/uploads/...
        if parts[:1] == ["static"] and len(parts) >= 2 and parts[1] == "uploads":
            parts = parts[2:]
        elif parts[:1] == ["uploads"]:
            parts = parts[1:]
        else:
            continue
        if not parts or info.is_dir():
            continue
        if any(part in ("..", "") for part in parts):
            raise BackupError("Backup archive contains unsafe upload paths.")
        relative = Path(*parts)
        dest = (uploads_root / relative).resolve()
        try:
            dest.relative_to(uploads_root)
        except ValueError as exc:
            raise BackupError("Backup archive contains invalid upload paths.") from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zip_file.open(info, "r") as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
        restored += 1
    return restored


def _restore_from_zip_path(zip_path: Path) -> dict[str, Any]:
    """Restore database and uploads from a zip file already on disk."""
    if not zip_path.exists():
        raise BackupError("Backup file not found.")
    if not zipfile.is_zipfile(zip_path):
        raise BackupError("Provided file is not a valid zip archive.")
    work_dir = Path(tempfile.mkdtemp(prefix="exam_restore_work_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            db_name = _restore_sqlite_from_zip(zf, work_dir)
            uploads_restored = _restore_uploads_from_zip(zf)
        return {"database": db_name, "uploads_restored": uploads_restored}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def restore_backup_upload(file_storage) -> dict[str, Any]:
    """Restore a backup from an uploaded zip file (Werkzeug FileStorage)."""
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise BackupError("Please upload a backup zip file.")
    fd, tmp_name = tempfile.mkstemp(prefix="exam_restore_", suffix=".zip")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        file_storage.save(tmp_path)
        return _restore_from_zip_path(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def restore_backup_file(zip_path: Path) -> dict[str, Any]:
    """Restore a backup from an existing zip on disk (e.g., from backup history)."""
    return _restore_from_zip_path(zip_path)


def purge_all_data() -> dict[str, Any]:
    """
    Remove all application data and files (database + uploads) and recreate defaults.
    Intended for SQLite deployments; falls back to drop_all if file is missing.
    """
    db_path = _sqlite_database_path()

    # Stop active sessions before altering files.
    db.session.remove()
    db.engine.dispose()

    # Try to drop schema first (works even if file cannot be deleted on Windows).
    try:
        db.drop_all()
    except Exception as exc:  # pylint: disable=broad-except
        raise BackupError(f"Failed to drop database schema: {exc}") from exc

    # Then attempt to remove SQLite files for a clean slate.
    if db_path and db_path.exists():
        for candidate in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError as exc:
                # On Windows a locked file cannot be deleted; warn but continue after drop_all.
                current_app.logger.warning("Could not remove database file %s: %s", candidate, exc)

    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    uploads_removed = 0
    if uploads_root.exists():
        for child in uploads_root.iterdir():
            try:
                if child.is_file() or child.is_symlink():
                    child.unlink()
                else:
                    shutil.rmtree(child, ignore_errors=True)
                uploads_removed += 1
            except OSError:
                current_app.logger.warning("Could not remove %s", child)
    uploads_root.mkdir(parents=True, exist_ok=True)

    try:
        init_db()
    except Exception as exc:  # pylint: disable=broad-except
        raise BackupError(
            f"Failed to reinitialize database: {exc}. If you are on Windows, ensure no other process (another server instance or shell) is holding the database file and retry."
        ) from exc

    return {"uploads_removed": uploads_removed, "db_reset": True}

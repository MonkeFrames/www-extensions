import hmac
import io
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import bleach
import markdown
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError

from auth import discord_auth
from extension import Extension, load_extensions, save_extension

load_dotenv()

ROOT = Path(__file__).resolve().parent
ROOT_ADMIN_ID = "514514780061696001"
app = Flask(__name__, instance_relative_config=True)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
database_path = Path(os.environ.get("DATABASE_PATH", Path(app.instance_path) / "extensions.sqlite3"))
if not database_path.is_absolute():
    database_path = ROOT / database_path
upload_path = Path(app.instance_path) / "extension_uploads"
upload_path.mkdir(parents=True, exist_ok=True)
database_path.parent.mkdir(parents=True, exist_ok=True)

app.config.update(
    DATABASE=str(database_path),
    UPLOAD_FOLDER=str(upload_path),
    MAX_CONTENT_LENGTH=140 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true",
)
app.register_blueprint(discord_auth)


@contextmanager
def _db_connect():
    db = sqlite3.connect(app.config["DATABASE"])
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_admin_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            user_id TEXT PRIMARY KEY,
            added_by TEXT NOT NULL,
            added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id TEXT PRIMARY KEY,
            banned_by TEXT NOT NULL,
            banned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS verified_developers (
            user_id TEXT PRIMARY KEY,
            verified_by TEXT NOT NULL,
            verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
        (ROOT_ADMIN_ID, ROOT_ADMIN_ID),
    )


def _is_admin(user_id: str) -> bool:
    if not user_id:
        return False
    with _db_connect() as db:
        _ensure_admin_schema(db)
        return db.execute("SELECT 1 FROM admins WHERE user_id = ?", (str(user_id),)).fetchone() is not None


def _is_banned(user_id: str) -> bool:
    if not user_id:
        return False
    with _db_connect() as db:
        _ensure_admin_schema(db)
        return db.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (str(user_id),)).fetchone() is not None


def _is_verified_developer(user_id: str) -> bool:
    if not user_id:
        return False
    with _db_connect() as db:
        _ensure_admin_schema(db)
        return db.execute(
            "SELECT 1 FROM verified_developers WHERE user_id = ?", (str(user_id),)
        ).fetchone() is not None


def _require_admin() -> str:
    user = session.get("discord_user")
    if not user:
        abort(401, description="Sign in with Discord to access the admin panel.")
    user_id = str(user.get("id", ""))
    if not _is_admin(user_id):
        abort(403, description="Admin access required.")
    return user_id


def _require_admin_csrf() -> str:
    actor_id = _require_admin()
    expected = session.get("admin_csrf_token", "")
    submitted = request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, submitted):
        abort(400, description="Admin form expired. Reload the page and try again.")
    return actor_id


def _extension_files(db: sqlite3.Connection, extension_id: str) -> list[str]:
    row = db.execute(
        "SELECT icon, screenshots, download FROM extensions WHERE id = ?",
        (extension_id,),
    ).fetchone()
    if not row:
        return []
    return [row[0], row[2], *json.loads(row[1] or "[]")]


def _remove_upload_files(references: list[str]) -> None:
    for reference in references:
        filename = Path(reference).name
        if filename and filename != "icon.png":
            (Path(app.config["UPLOAD_FOLDER"]) / filename).unlink(missing_ok=True)


@app.context_processor
def inject_admin_flags():
    user = session.get("discord_user") or {}
    user_id = str(user.get("id", ""))
    admin = _is_admin(user_id) if user_id else False
    verified = _is_verified_developer(user_id) if user_id else False
    return {
        "is_admin": admin,
        "is_root_admin": admin and user_id == ROOT_ADMIN_ID,
        "is_verified_developer": verified,
    }


def _render_markdown(source: str) -> str:
    rendered = markdown.markdown(source, extensions=["fenced_code", "tables", "sane_lists"])
    allowed_tags = set(bleach.sanitizer.ALLOWED_TAGS) | {
        "p", "h1", "h2", "h3", "h4", "hr", "pre", "code", "blockquote",
        "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "br",
    }
    allowed_attributes = {"a": ["href", "title"], "code": ["class"], "th": ["align"], "td": ["align"]}
    return bleach.clean(
        rendered,
        tags=allowed_tags,
        attributes=allowed_attributes,
        protocols={"http", "https", "mailto"},
        strip=True,
    )


def _image_bytes(file_storage, max_dimension: int) -> bytes:
    extension = Path(file_storage.filename or "").suffix.lower()
    formats = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
    expected_format = formats.get(extension)
    if expected_format is None:
        raise ValueError("Use a JPG, JPEG, PNG, or WEBP image.")

    raw = file_storage.stream.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Each image must be 5 MB or smaller.")
    if not raw:
        raise ValueError("The selected image is empty.")

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
        if image_format != expected_format:
            raise ValueError("The image content does not match its file extension.")
        if width < 50 or height < 50:
            raise ValueError("Images must be at least 50 × 50 pixels.")
        if width > max_dimension or height > max_dimension:
            raise ValueError(f"Images cannot exceed {max_dimension} pixels in either dimension.")

        with Image.open(io.BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image)
            if expected_format == "JPEG":
                image = image.convert("RGB")
            elif expected_format == "PNG" and image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")
            elif expected_format == "WEBP" and image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")
            output = io.BytesIO()
            save_options = {"quality": 88} if expected_format in {"JPEG", "WEBP"} else {"optimize": True}
            if expected_format == "JPEG":
                save_options["optimize"] = True
            if expected_format == "WEBP":
                save_options["method"] = 4
            image.save(output, format=expected_format, **save_options)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The selected file is not a valid supported image.") from error


def _save_upload(filename: str, contents: bytes) -> None:
    destination = Path(app.config["UPLOAD_FOLDER"]) / filename
    with destination.open("xb") as output:
        output.write(contents)


@app.route("/")
def root():
    return redirect(url_for("browse"), code=302)


@app.route("/browse")
def browse():
    with _db_connect() as db:
        extensions = load_extensions(db, status="approved")
        _ensure_admin_schema(db)
        verified_ids = {row[0] for row in db.execute("SELECT user_id FROM verified_developers")}
    for extension in extensions:
        extension.description_html = _render_markdown(extension.description)
        extension.is_verified_developer = extension.author_id in verified_ids
    return render_template("browse.html", extensions=extensions)


def _api_extension_data(extension: Extension, verified_ids: set[str], *, full: bool = False) -> dict:
    def absolute_url(value: str) -> str:
        if value.startswith("/"):
            return request.host_url.rstrip("/") + value
        return value

    data = {
        "id": extension.id,
        "name": extension.name,
        "version": extension.version,
        "publisher": extension.author_name,
        "publisher_id": extension.author_id,
        "verified": extension.author_id in verified_ids,
        "icon": absolute_url(extension.icon),
        "summary": extension.summary,
        "category": extension.category,
        "updated": extension.updated,
    }
    if full:
        data.update({
            "description": extension.description,
            "changelog": extension.changelog,
            "download": absolute_url(extension.download),
            "screenshots": [absolute_url(image) for image in extension.screenshots],
            "requirements": extension.requirements,
        })
    return data


@app.get("/api/gallery")
def api_gallery():
    with _db_connect() as db:
        extensions = load_extensions(db, status="approved")
        _ensure_admin_schema(db)
        verified_ids = {row[0] for row in db.execute("SELECT user_id FROM verified_developers")}
    return jsonify({"extensions": [_api_extension_data(extension, verified_ids) for extension in extensions]})


@app.get("/api/extension")
def api_extension():
    extension_id = request.args.get("id", "").strip()
    if not extension_id:
        return jsonify({"error": "Missing required query parameter: id"}), 400
    with _db_connect() as db:
        extensions = load_extensions(db, status="approved")
        extension = next((item for item in extensions if item.id == extension_id), None)
        if extension is None:
            return jsonify({"error": "Approved extension not found"}), 404
        _ensure_admin_schema(db)
        verified_ids = {row[0] for row in db.execute("SELECT user_id FROM verified_developers")}
    return jsonify(_api_extension_data(extension, verified_ids, full=True))


@app.route("/upload", methods=["GET", "POST"])
def upload():
    discord_user = session.get("discord_user")
    if not discord_user:
        if request.method == "POST":
            return redirect(url_for("discord_auth.login", next="/upload"))
        return render_template("upload.html", discord_user=None, banned=False)

    if _is_banned(str(discord_user.get("id", ""))):
        return render_template("upload.html", discord_user=discord_user, banned=True)

    author_id = str(discord_user.get("id", ""))
    with _db_connect() as db:
        owned_extensions = [
            extension for extension in load_extensions(db, status=None)
            if extension.author_id == author_id
        ]
    owned_by_id = {extension.id: extension for extension in owned_extensions}

    if request.method == "GET":
        selected_id = request.args.get("edit", "").strip()
        selected_extension = owned_by_id.get(selected_id) if selected_id else None
        if selected_id and selected_extension is None:
            abort(404, description="That extension does not belong to your account.")
        csrf_token = session.setdefault("upload_csrf_token", secrets.token_urlsafe(32))
        return render_template(
            "upload.html", discord_user=discord_user, csrf_token=csrf_token, banned=False,
            owned_extensions=owned_extensions, selected_extension=selected_extension,
        )

    submitted_token = request.form.get("csrf_token", "")
    expected_token = session.get("upload_csrf_token", "")
    if not expected_token or not hmac.compare_digest(expected_token, submitted_token):
        abort(400, description="Upload form expired. Reload the page and try again.")

    selected_id = request.form.get("extension_id", "").strip()
    selected_extension = owned_by_id.get(selected_id) if selected_id else None
    if selected_id and selected_extension is None:
        abort(404, description="That extension does not belong to your account.")
    edit_query = {"edit": selected_id} if selected_id else {}
    name = request.form.get("name", "").strip()
    summary = request.form.get("summary", "").strip()
    description = request.form.get("description", "").strip()
    version = request.form.get("version", "").strip()
    changelog = request.form.get("changelog", "").strip() if selected_extension else ""
    if selected_extension and (not changelog or len(changelog) > 5000):
        flash("Enter a changelog of 1–5,000 characters for this update.", "error")
        return redirect(url_for("upload", **edit_query))
    if not version or len(version) > 32:
        flash("Enter a version of 1–32 characters.", "error")
        return redirect(url_for("upload", **edit_query))
    if not name or len(name) > 100:
        flash("Enter a mod name of 1–100 characters.", "error")
        return redirect(url_for("upload", **edit_query))
    if not summary or len(summary) > 300:
        flash("Enter a short summary of 1–300 characters.", "error")
        return redirect(url_for("upload", **edit_query))
    if not description or len(description) > 20000:
        flash("Enter a description of 1–20,000 characters.", "error")
        return redirect(url_for("upload", **edit_query))

    package = request.files.get("extension_file")
    if not package or not package.filename or Path(package.filename).suffix.lower() != ".mfextension":
        flash("Choose an extension file ending in .mfextension.", "error")
        return redirect(url_for("upload", **edit_query))
    package.stream.seek(0, os.SEEK_END)
    package_size = package.stream.tell()
    package.stream.seek(0)
    if package_size < 1 or package_size > 100 * 1024 * 1024:
        flash("The extension file must be smaller than 100 MB.", "error")
        return redirect(url_for("upload", **edit_query))

    screenshot_files = [file for file in request.files.getlist("screenshots") if file and file.filename]
    if len(screenshot_files) > 5:
        flash("Upload no more than five screenshots.", "error")
        return redirect(url_for("upload", **edit_query))

    icon_file = request.files.get("icon")
    if icon_file and not icon_file.filename:
        icon_file = None

    try:
        icon_data = _image_bytes(icon_file, 1024) if icon_file else None
        screenshots_data = [_image_bytes(file, 4096) for file in screenshot_files]
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("upload", **edit_query))

    extension_id = selected_id or uuid4().hex
    upload_token = uuid4().hex
    stored_names = []
    old_files = []
    try:
        package_name = f"{extension_id}-{upload_token}.mfextension"
        package.save(Path(app.config["UPLOAD_FOLDER"]) / package_name)
        stored_names.append(package_name)

        icon_url = selected_extension.icon if selected_extension else url_for("static", filename="img/icon.png")
        if icon_data:
            icon_suffix = Path(icon_file.filename).suffix.lower()
            icon_name = f"{extension_id}-{upload_token}-icon{icon_suffix}"
            _save_upload(icon_name, icon_data)
            stored_names.append(icon_name)
            icon_url = url_for("extension_media", filename=icon_name)

        screenshot_urls = list(selected_extension.screenshots) if selected_extension else []
        if screenshot_files:
            screenshot_urls = []
        for index, (file, contents) in enumerate(zip(screenshot_files, screenshots_data), start=1):
            screenshot_name = f"{extension_id}-{upload_token}-screenshot-{index}{Path(file.filename).suffix.lower()}"
            _save_upload(screenshot_name, contents)
            stored_names.append(screenshot_name)
            screenshot_urls.append(url_for("extension_media", filename=screenshot_name))

        extension = Extension(
            id=extension_id,
            name=name,
            version=version,
            category=selected_extension.category if selected_extension else "Extensions",
            summary=summary,
            description=description,
            author_name=discord_user.get("username", ""),
            author_id=str(discord_user.get("id", "")),
            updated=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            download=url_for("extension_media", filename=package_name),
            icon=icon_url,
            screenshots=screenshot_urls,
            status="pending_review",
            changelog=changelog,
        )
        with _db_connect() as db:
            if selected_extension:
                old_files = _extension_files(db, extension_id)
            save_extension(db, extension)
    except Exception:
        for filename in stored_names:
            (Path(app.config["UPLOAD_FOLDER"]) / filename).unlink(missing_ok=True)
        app.logger.exception("Could not store extension submission")
        flash("The upload could not be saved. Please try again.", "error")
        return redirect(url_for("upload", **edit_query))

    if old_files:
        new_references = {extension.download, extension.icon, *extension.screenshots}
        _remove_upload_files([reference for reference in old_files if reference not in new_references])

    session.pop("upload_csrf_token", None)
    if selected_extension:
        flash("Extension update submitted for review. It will appear after approval.", "success")
    else:
        flash("Extension submitted for review. It will appear in the gallery after approval.", "success")
    return redirect(url_for("upload"))


@app.route("/media/<filename>")
def extension_media(filename: str):
    if Path(filename).name != filename:
        abort(404)
    current_user_id = str((session.get("discord_user") or {}).get("id", ""))
    current_user_is_admin = _is_admin(current_user_id) if current_user_id else False
    allowed = False
    with _db_connect() as db:
        for row in db.execute("SELECT author_id, status, icon, screenshots, download FROM extensions"):
            references = [row[2], row[4]] + json.loads(row[3] or "[]")
            if any(Path(urlsplit_path).name == filename for urlsplit_path in references):
                allowed = row[1] == "approved" or (current_user_id and (row[0] == current_user_id or current_user_is_admin))
                break
    if not allowed:
        abort(404)
    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        filename,
        as_attachment=filename.endswith(".mfextension"),
        max_age=3600,
    )


def _valid_discord_id(user_id: str) -> bool:
    return user_id.isascii() and user_id.isdecimal() and 17 <= len(user_id) <= 20


@app.route("/admin")
def admin_panel():
    user = session.get("discord_user")
    if not user:
        return redirect(url_for("discord_auth.login", next="/admin"))
    actor_id = _require_admin()
    csrf_token = session.setdefault("admin_csrf_token", secrets.token_urlsafe(32))

    with _db_connect() as db:
        _ensure_admin_schema(db)
        extensions = load_extensions(db, status=None)
        users_by_id = {}
        for user_id, username, count in db.execute(
            """
            SELECT author_id, MAX(author_name), COUNT(*)
            FROM extensions WHERE author_id != ''
            GROUP BY author_id
            """
        ):
            users_by_id[user_id] = {"id": user_id, "username": username or "Discord user", "extension_count": count}
        banned_ids = {row[0] for row in db.execute("SELECT user_id FROM banned_users")}
        for user_id in banned_ids:
            users_by_id.setdefault(user_id, {"id": user_id, "username": "Discord user", "extension_count": 0})
        verified_ids = {row[0] for row in db.execute("SELECT user_id FROM verified_developers")}
        for user_id in verified_ids:
            users_by_id.setdefault(user_id, {"id": user_id, "username": "Discord user", "extension_count": 0})
        admins = [row[0] for row in db.execute("SELECT user_id FROM admins ORDER BY added_at")]

    for extension in extensions:
        extension.description_html = _render_markdown(extension.description)
        extension.is_verified_developer = extension.author_id in verified_ids
    users = sorted(users_by_id.values(), key=lambda item: item["username"].casefold())
    for user in users:
        user["verified"] = user["id"] in verified_ids
    return render_template(
        "admin.html",
        extensions=extensions,
        users=users,
        banned_ids=banned_ids,
        verified_ids=verified_ids,
        admins=admins,
        csrf_token=csrf_token,
        actor_id=actor_id,
        root_admin_id=ROOT_ADMIN_ID,
    )


@app.post("/admin/extensions/<extension_id>/status")
def admin_set_extension_status(extension_id: str):
    _require_admin_csrf()
    status = request.form.get("status")
    if status not in {"approved", "unapproved"}:
        abort(400, description="Choose approved or unapproved status.")
    with _db_connect() as db:
        load_extensions(db, status=None)
        cursor = db.execute("UPDATE extensions SET status = ? WHERE id = ?", (status, extension_id))
        if not cursor.rowcount:
            abort(404)
    flash(f"Extension status set to {status}.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/extensions/<extension_id>/delete")
def admin_delete_extension(extension_id: str):
    _require_admin_csrf()
    with _db_connect() as db:
        load_extensions(db, status=None)
        files = _extension_files(db, extension_id)
        if not files:
            abort(404)
        db.execute("DELETE FROM extensions WHERE id = ?", (extension_id,))
    _remove_upload_files(files)
    flash("Extension and uploaded files deleted.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/users/<user_id>/ban")
def admin_ban_user(user_id: str):
    actor_id = _require_admin_csrf()
    if not _valid_discord_id(user_id) or user_id == ROOT_ADMIN_ID:
        abort(400, description="That user cannot be banned.")
    with _db_connect() as db:
        _ensure_admin_schema(db)
        db.execute(
            "INSERT OR IGNORE INTO banned_users (user_id, banned_by) VALUES (?, ?)",
            (user_id, actor_id),
        )
    flash("User banned from submitting extensions.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/users/ban")
def admin_ban_user_by_id():
    actor_id = _require_admin_csrf()
    user_id = request.form.get("user_id", "").strip()
    if not _valid_discord_id(user_id) or user_id == ROOT_ADMIN_ID:
        flash("Enter a valid Discord user ID. The root admin cannot be banned.", "error")
        return redirect(url_for("admin_panel"))
    with _db_connect() as db:
        _ensure_admin_schema(db)
        db.execute(
            "INSERT OR IGNORE INTO banned_users (user_id, banned_by) VALUES (?, ?)",
            (user_id, actor_id),
        )
    flash("User banned from submitting extensions.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/users/<user_id>/unban")
def admin_unban_user(user_id: str):
    _require_admin_csrf()
    if not _valid_discord_id(user_id):
        abort(400)
    with _db_connect() as db:
        _ensure_admin_schema(db)
        db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
    flash("User ban removed.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/users/<user_id>/verify")
def admin_verify_developer(user_id: str):
    actor_id = _require_admin_csrf()
    if not _valid_discord_id(user_id):
        abort(400, description="Enter a valid Discord user ID.")
    with _db_connect() as db:
        _ensure_admin_schema(db)
        db.execute(
            "INSERT OR IGNORE INTO verified_developers (user_id, verified_by) VALUES (?, ?)",
            (user_id, actor_id),
        )
    flash("Developer verified.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/users/verify")
def admin_verify_developer_by_id():
    actor_id = _require_admin_csrf()
    user_id = request.form.get("user_id", "").strip()
    if not _valid_discord_id(user_id):
        flash("Enter a valid Discord user ID.", "error")
        return redirect(url_for("admin_panel"))
    with _db_connect() as db:
        _ensure_admin_schema(db)
        db.execute(
            "INSERT OR IGNORE INTO verified_developers (user_id, verified_by) VALUES (?, ?)",
            (user_id, actor_id),
        )
    flash("Developer verified.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/users/<user_id>/unverify")
def admin_unverify_developer(user_id: str):
    _require_admin_csrf()
    if not _valid_discord_id(user_id):
        abort(400, description="Enter a valid Discord user ID.")
    with _db_connect() as db:
        _ensure_admin_schema(db)
        db.execute("DELETE FROM verified_developers WHERE user_id = ?", (user_id,))
    flash("Developer verification removed.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/users/<user_id>/delete-data")
def admin_delete_user_data(user_id: str):
    _require_admin_csrf()
    if not _valid_discord_id(user_id) or user_id == ROOT_ADMIN_ID:
        abort(400, description="That account data cannot be deleted from this panel.")
    with _db_connect() as db:
        load_extensions(db, status=None)
        rows = db.execute("SELECT id FROM extensions WHERE author_id = ?", (user_id,)).fetchall()
        files = []
        for (extension_id,) in rows:
            files.extend(_extension_files(db, extension_id))
        db.execute("DELETE FROM extensions WHERE author_id = ?", (user_id,))
    _remove_upload_files(files)
    flash("All extension records and uploaded files for that user were deleted.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/admins")
def admin_add_admin():
    actor_id = _require_admin_csrf()
    if actor_id != ROOT_ADMIN_ID:
        abort(403, description="Only the root admin can add admins.")
    target_id = request.form.get("user_id", "").strip()
    if not _valid_discord_id(target_id):
        flash("Enter a valid Discord user ID.", "error")
        return redirect(url_for("admin_panel"))
    with _db_connect() as db:
        _ensure_admin_schema(db)
        db.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
            (target_id, actor_id),
        )
    flash("Admin access granted.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/admins/<user_id>/remove")
def admin_remove_admin(user_id: str):
    actor_id = _require_admin_csrf()
    if actor_id != ROOT_ADMIN_ID:
        abort(403, description="Only the root admin can remove admins.")
    if not _valid_discord_id(user_id) or user_id == ROOT_ADMIN_ID:
        abort(400, description="The root admin cannot be removed.")
    with _db_connect() as db:
        _ensure_admin_schema(db)
        db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    flash("Admin access removed.", "success")
    return redirect(url_for("admin_panel"))


if __name__ == "__main__":
    app.run(port=3500)

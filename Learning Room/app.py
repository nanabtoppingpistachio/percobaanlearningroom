from flask import Flask, render_template, request, redirect, session, url_for, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pathlib import Path
import sqlite3
import uuid


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

TEMPLATE_DIR = PROJECT_DIR / "templates"
STATIC_DIR = PROJECT_DIR / "static"
UPLOAD_FOLDER = STATIC_DIR / "uploads"
MATERIAL_FOLDER = STATIC_DIR / "materials"
DATABASE = BASE_DIR / "users.db"


TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
MATERIAL_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
    static_url_path="/static",
)
app.secret_key = "change-this-key-before-publishing"
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024


def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                bio TEXT NOT NULL DEFAULT '',
                avatar_filename TEXT,
                banner_filename TEXT
            )
        """)
        
        db.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                deadline TEXT NOT NULL DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        for column, definition in (
            ("bio", "TEXT NOT NULL DEFAULT ''"),
            ("avatar_filename", "TEXT"),
            ("banner_filename", "TEXT"),
        ):
            if column not in columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

        db.execute("""
            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                material_type TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                privacy TEXT NOT NULL DEFAULT 'Private',
                file_name TEXT,
                stored_name TEXT,
                external_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        db.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def image_url(filename):
    if not filename:
        return None
    # A cache-busting version avoids the browser reusing an old image.
    return url_for("static", filename=f"uploads/{filename}", v=filename)


@app.route("/")
def home():
    user = current_user()
    if not user:
        return redirect(url_for("auth"))
    return render_template(
        "index.html",
        profile_name=user["name"],
        profile_bio=user["bio"],
        avatar_url=image_url(user["avatar_filename"]),
        banner_url=image_url(user["banner_filename"]),
    )


@app.route("/auth", methods=["GET", "POST"])
def auth():
    if session.get("user_id"):
        return redirect(url_for("home"))

    error = ""
    mode = request.form.get("mode", request.args.get("mode", "login"))
    if mode not in {"login", "register"}:
        mode = "login"

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            error = "Email dan password wajib diisi."
        elif mode == "register":
            if not name or len(name) > 50:
                error = "Nama wajib diisi (maksimal 50 karakter)."
            elif len(password) < 6:
                error = "Password minimal 6 karakter."
            else:
                try:
                    with get_db() as db:
                        cur = db.execute(
                            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                            (name, email, generate_password_hash(password)),
                        )
                        session["user_id"] = cur.lastrowid
                        session["user_name"] = name
                    return redirect(url_for("home"))
                except sqlite3.IntegrityError:
                    error = "Email ini sudah terdaftar. Silakan login."
        else:
            with get_db() as db:
                user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                return redirect(url_for("home"))
            error = "Email atau password tidak cocok."

    return render_template("auth.html", error=error, mode=mode)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth"))
@app.route("/api/materials", methods=["GET", "POST"])
def api_materials():
    user = current_user()

    if not user:
        return {"error": "Silakan login terlebih dahulu."}, 401

    if request.method == "GET":
        with get_db() as db:
            rows = db.execute(
                "SELECT * FROM materials WHERE user_id = ? ORDER BY id DESC",
                (user["id"],)
            ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item["fileUrl"] = (
                url_for("material_file", material_id=item["id"])
                if item["stored_name"] else ""
            )
            item["viewUrl"] = url_for(
                "view_material", material_id=item["id"]
            )
            item["fileName"] = item["file_name"] or ""
            item["type"] = item["material_type"] or "Other"
            item["url"] = item["external_url"] or ""
            result.append(item)

        return {"materials": result}

    title = request.form.get("title", "").strip()
    material_type = request.form.get("type", "Other").strip()
    subject = request.form.get("subject", "").strip()
    privacy = request.form.get("privacy", "Private")
    external_url = request.form.get("url", "").strip()
    upload = request.files.get("file")

    if not title:
        return {"error": "Judul materi wajib diisi."}, 400

    if privacy not in ("Private", "Public"):
        privacy = "Private"

    if external_url and not external_url.startswith(("http://", "https://")):
        return {"error": "Tautan harus diawali http:// atau https://."}, 400

    original_name = None
    stored_name = None

    if upload and upload.filename:
        original_name = secure_filename(upload.filename)

        if not original_name or "." not in original_name:
            return {"error": "Nama file tidak valid."}, 400

        allowed = {
            "pdf", "ppt", "pptx", "pps", "ppsx", "doc", "docx",
            "odt", "txt", "mp4", "webm", "mov", "m4v", "mp3",
            "wav", "ogg", "png", "jpg", "jpeg", "webp", "gif",
            "csv", "xls", "xlsx", "zip"
        }

        extension = original_name.rsplit(".", 1)[1].lower()

        if extension not in allowed:
            return {"error": "Format file belum didukung."}, 400

        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)

        if size > 95 * 1024 * 1024:
            return {"error": "Ukuran file maksimal 95 MB."}, 413

        stored_name = (
            f"user_{user['id']}_{uuid.uuid4().hex}.{extension}"
        )
        upload.save(MATERIAL_FOLDER / stored_name)

    if not stored_name and not external_url:
        return {
            "error": "Pilih file atau masukkan tautan materi."
        }, 400

    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO materials
            (user_id, title, material_type, subject, privacy,
             file_name, stored_name, external_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], title, material_type, subject, privacy,
                original_name, stored_name, external_url
            )
        )

        row = db.execute(
            "SELECT * FROM materials WHERE id = ?",
            (cursor.lastrowid,)
        ).fetchone()

    item = dict(row)
    item["fileUrl"] = (
        url_for("material_file", material_id=item["id"])
        if item["stored_name"] else ""
    )
    item["viewUrl"] = url_for("view_material", material_id=item["id"])
    item["fileName"] = item["file_name"] or ""
    item["type"] = item["material_type"] or "Other"
    item["url"] = item["external_url"] or ""

    return item, 201
    
@app.route("/api/notes", methods=["GET", "POST"])
def api_notes():
    user = current_user()
    if not user:
        return {"error": "Silakan login terlebih dahulu."}, 401

    if request.method == "GET":
        with get_db() as db:
            rows = db.execute(
                """SELECT id, title, subject, body, created_at
                   FROM notes WHERE user_id = ?
                   ORDER BY id DESC""",
                (user["id"],)
            ).fetchall()
        return {"notes": [dict(row) for row in rows]}

    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    subject = str(data.get("subject", "")).strip()
    body = str(data.get("body", "")).strip()

    if not title or not body:
        return {"error": "Judul dan isi catatan wajib diisi."}, 400
    if len(title) > 150 or len(subject) > 100 or len(body) > 10000:
        return {"error": "Catatan terlalu panjang."}, 400

    with get_db() as db:
        cur = db.execute(
            """INSERT INTO notes (user_id, title, subject, body)
               VALUES (?, ?, ?, ?)""",
            (user["id"], title, subject, body)
        )
        row = db.execute(
            """SELECT id, title, subject, body, created_at
               FROM notes WHERE id = ? AND user_id = ?""",
            (cur.lastrowid, user["id"])
        ).fetchone()

    return dict(row), 201


@app.route("/api/notes/<int:note_id>", methods=["PUT", "DELETE"])
def manage_note(note_id):
    user = current_user()
    if not user:
        return {"error": "Silakan login terlebih dahulu."}, 401

    with get_db() as db:
        note = db.execute(
            "SELECT id FROM notes WHERE id = ? AND user_id = ?",
            (note_id, user["id"])
        ).fetchone()

        if not note:
            return {"error": "Catatan tidak ditemukan."}, 404

        if request.method == "DELETE":
            db.execute(
                "DELETE FROM notes WHERE id = ? AND user_id = ?",
                (note_id, user["id"])
            )
            return {"message": "Catatan berhasil dihapus."}

        data = request.get_json(silent=True) or {}
        title = str(data.get("title", "")).strip()
        subject = str(data.get("subject", "")).strip()
        body = str(data.get("body", "")).strip()

        if not title or not body:
            return {"error": "Judul dan isi catatan wajib diisi."}, 400

        if len(title) > 150 or len(subject) > 100 or len(body) > 10000:
            return {"error": "Catatan terlalu panjang."}, 400

        db.execute(
            """UPDATE notes
               SET title = ?, subject = ?, body = ?
               WHERE id = ? AND user_id = ?""",
            (title, subject, body, note_id, user["id"])
        )

    return {"message": "Catatan berhasil diperbarui."}

@app.route("/api/todos", methods=["GET", "POST"])
def api_todos():
    user = current_user()
    if not user:
        return {"error": "Silakan login terlebih dahulu."}, 401

    if request.method == "GET":
        with get_db() as db:
            rows = db.execute(
                """SELECT id, title, deadline, completed
                   FROM todos
                   WHERE user_id = ?
                   ORDER BY id DESC""",
                (user["id"],)
            ).fetchall()
        return {"todos": [dict(row) for row in rows]}

    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    deadline = str(data.get("deadline", "")).strip()

    if not title:
        return {"error": "Nama tugas wajib diisi."}, 400
    if len(title) > 200 or len(deadline) > 10:
        return {"error": "Nama tugas atau deadline terlalu panjang."}, 400

    with get_db() as db:
        cur = db.execute(
            """INSERT INTO todos (user_id, title, deadline)
               VALUES (?, ?, ?)""",
            (user["id"], title, deadline)
        )
        row = db.execute(
            """SELECT id, title, deadline, completed
               FROM todos WHERE id = ? AND user_id = ?""",
            (cur.lastrowid, user["id"])
        ).fetchone()

    return dict(row), 201

@app.route("/api/todos/<int:todo_id>", methods=["PATCH", "DELETE"])
def manage_todo(todo_id):
    user = current_user()
    if not user:
        return {"error": "Silakan login terlebih dahulu."}, 401

    with get_db() as db:
        todo = db.execute(
            "SELECT id FROM todos WHERE id = ? AND user_id = ?",
            (todo_id, user["id"])
        ).fetchone()

        if not todo:
            return {"error": "Tugas tidak ditemukan."}, 404

        if request.method == "DELETE":
            db.execute(
                "DELETE FROM todos WHERE id = ? AND user_id = ?",
                (todo_id, user["id"])
            )
            return {"message": "Tugas berhasil dihapus."}

        data = request.get_json(silent=True) or {}
        completed = data.get("completed")

        if completed not in (0, 1, False, True):
            return {"error": "Status tugas tidak valid."}, 400

        db.execute(
            "UPDATE todos SET completed = ? WHERE id = ? AND user_id = ?",
            (int(completed), todo_id, user["id"])
        )

    return {"message": "Status tugas berhasil diperbarui."}

@app.route("/materials/file/<int:material_id>")
def material_file(material_id):
    user = current_user()

    if not user:
        return redirect(url_for("auth"))

    with get_db() as db:
        material = db.execute(
            "SELECT * FROM materials WHERE id = ? AND user_id = ?",
            (material_id, user["id"])
        ).fetchone()

    if not material or not material["stored_name"]:
        abort(404)

    return send_from_directory(
        MATERIAL_FOLDER,
        material["stored_name"],
        as_attachment=request.args.get("download") == "1",
        download_name=material["file_name"] or material["stored_name"]
    )


@app.route("/materials/view/<int:material_id>")
def view_material(material_id):
    user = current_user()

    if not user:
        return redirect(url_for("auth"))

    with get_db() as db:
        material = db.execute(
            "SELECT * FROM materials WHERE id = ? AND user_id = ?",
            (material_id, user["id"])
        ).fetchone()

    if not material:
        abort(404)

    item = dict(material)
    filename = (item.get("file_name") or "").lower()
    extension = filename.rsplit(".", 1)[-1] if "." in filename else ""

    if extension in ("mp4", "webm", "mov", "m4v"):
        item["kind"] = "video"
    elif extension in ("mp3", "wav", "ogg"):
        item["kind"] = "audio"
    elif extension == "pdf":
        item["kind"] = "pdf"
    elif extension in ("png", "jpg", "jpeg", "webp", "gif"):
        item["kind"] = "image"
    elif extension in ("ppt", "pptx", "pps", "ppsx", "doc", "docx", "xls", "xlsx", "odt"):
        item["kind"] = "office"
    else:
        item["kind"] = "other"

    item["url"] = item.get("external_url") or ""

    return render_template("material_view.html", item=item)


@app.route("/update-profile", methods=["POST"])
def update_profile():
    user = current_user()
    if not user:
        return redirect(url_for("auth"))

    name = request.form.get("name", "").strip()
    bio = request.form.get("bio", "").strip()
    if not name or len(name) > 50:
        return "Nama harus diisi dan maksimal 50 karakter.", 400
    if len(bio) > 300:
        return "Bio maksimal 300 karakter.", 400

    avatar = request.files.get("avatarFile")
    banner = request.files.get("coverFile")
    new_avatar = user["avatar_filename"]
    new_banner = user["banner_filename"]
    saved_files = []

    try:
        for field_name, file in (("avatarFile", avatar), ("coverFile", banner)):
            if not file or not file.filename:
                continue
            safe_name = secure_filename(file.filename)
            if not safe_name or not allowed_image(safe_name):
                return "Format gambar harus PNG, JPG, JPEG, atau WEBP.", 400

            file.stream.seek(0, 2)
            size = file.stream.tell()
            file.stream.seek(0)
            if size > MAX_IMAGE_SIZE:
                return "Ukuran setiap gambar maksimal 5 MB.", 400

            ext = Path(safe_name).suffix.lower()
            filename = f"{uuid.uuid4().hex}{ext}"
            destination = UPLOAD_FOLDER / filename
            file.save(destination)
            saved_files.append(destination)

            if field_name == "avatarFile":
                new_avatar = filename
            else:
                new_banner = filename

        with get_db() as db:
            db.execute(
                """UPDATE users
                   SET name = ?, bio = ?, avatar_filename = ?, banner_filename = ?
                   WHERE id = ?""",
                (name, bio, new_avatar, new_banner, session["user_id"]),
            )
        session["user_name"] = name
        return redirect(url_for("home"))
    except Exception:
        # Do not leave newly uploaded files behind if saving the profile fails.
        for path in saved_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        app.logger.exception("Could not save profile images")
        return "Gagal menyimpan foto profil. Coba lagi dan periksa terminal VS Code.", 500


@app.errorhandler(413)
def file_too_large(_error):
    return "Total ukuran upload terlalu besar. Gunakan gambar di bawah 5 MB masing-masing.", 413


init_db()

@app.route("/api/materials/<int:material_id>", methods=["DELETE"])
def delete_material(material_id):
    user = current_user()
    if not user:
        return {"error": "Silakan login terlebih dahulu."}, 401

    with get_db() as db:
        material = db.execute(
            "SELECT id, stored_name FROM materials WHERE id = ? AND user_id = ?",
            (material_id, user["id"])
        ).fetchone()

        if not material:
            return {"error": "Materi tidak ditemukan."}, 404

        db.execute(
            "DELETE FROM materials WHERE id = ? AND user_id = ?",
            (material_id, user["id"])
        )

    if material["stored_name"]:
        file_path = MATERIAL_FOLDER / material["stored_name"]
        file_path.unlink(missing_ok=True)

    return {"message": "Materi berhasil dihapus."}

if __name__ == "__main__":
    app.run(debug=True)

import os
import re
import sys
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import database


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.url_map.strict_slashes = False
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SITE_URL"] = os.environ.get("SITE_URL", "http://127.0.0.1:5000")
app.config["PUBLIC_URL"] = os.environ.get(
    "PUBLIC_URL", "https://sozeracke-blog.onrender.com"
)

IS_RENDER = bool(os.environ.get("RENDER"))

if IS_RENDER:
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    print(
        f"[blog] database={'postgresql' if database.USE_POSTGRES else 'sqlite (NOT PERSISTENT!)'}",
        flush=True,
    )

BASE_DIR = os.path.dirname(__file__)
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
MEDIA_FILENAME_RE = re.compile(
    r"^[a-f0-9]+\.(?:png|jpg|jpeg|gif|webp)$", re.IGNORECASE
)

URL_RE = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]]+|www\.[^\s<>"{}|\\^`\[\]]+',
    re.IGNORECASE,
)

OLD_DEFAULT_CATEGORIES = [
    "Технологии",
    "Жизнь",
    "Игры",
    "Творчество",
    "Новости",
    "Другое",
]

SITE_OWNER = os.environ.get("SITE_OWNER", "Sozeracke")
SITE_NAME = os.environ.get("SITE_NAME", "Sozeracke Private")
ONLINE_THRESHOLD_SECONDS = 300
LAST_SEEN_TOUCH_INTERVAL = 60

CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def get_db():
    if "db" not in g:
        g.db = database.connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def table_columns(db, table):
    return database.table_columns(db, table)


def slugify(text):
    chars = []
    for ch in text.lower().strip():
        if ch in CYRILLIC_TO_LATIN:
            chars.append(CYRILLIC_TO_LATIN[ch])
        elif ch.isalnum():
            chars.append(ch)
        elif ch in (" ", "-", "_"):
            chars.append("-")
    slug = re.sub(r"-+", "-", "".join(chars)).strip("-")
    if not slug:
        slug = f"item-{uuid.uuid4().hex[:8]}"
    return slug[:60]


def linkify(text):
    if not text:
        return ""
    escaped = str(escape(text))
    escaped = escaped.replace("\n", "<br>")

    def repl(match):
        url = match.group(0)
        href = url if url.lower().startswith("http") else f"https://{url}"
        return (
            f'<a href="{href}" target="_blank" rel="noopener noreferrer" '
            f'class="text-link">{url}</a>'
        )

    return Markup(URL_RE.sub(repl, escaped))


app.jinja_env.filters["linkify"] = linkify


def normalize_tag_name(name):
    name = name.strip()
    while name.startswith("#"):
        name = name[1:].strip()
    return name[:40]


def tag_display(name):
    return normalize_tag_name(name)


app.jinja_env.filters["tag_display"] = tag_display


def is_user_online(last_seen, user_id=None):
    if user_id and g.user and g.user["id"] == user_id:
        return True
    if not last_seen:
        return False
    try:
        seen = datetime.fromisoformat(last_seen)
    except ValueError:
        return False
    return (datetime.now() - seen).total_seconds() <= ONLINE_THRESHOLD_SECONDS


def touch_last_seen(user_id):
    now = datetime.now()
    last_touch = session.get("_last_seen_touch")
    if last_touch:
        try:
            if (now - datetime.fromisoformat(last_touch)).total_seconds() < LAST_SEEN_TOUCH_INTERVAL:
                return
        except ValueError:
            pass
    try:
        db = get_db()
        db.execute(
            "UPDATE users SET last_seen = ? WHERE id = ?",
            (now.isoformat(), user_id),
        )
        db.commit()
        session["_last_seen_touch"] = now.isoformat()
    except Exception:
        app.logger.exception("touch_last_seen failed for user %s", user_id)
        try:
            get_db()._conn.rollback()
        except Exception:
            pass


app.jinja_env.filters["user_online"] = is_user_online


def migrate_db():
    db = get_db()
    schema = database.postgres_schema() if database.USE_POSTGRES else database.sqlite_schema()
    db.executescript(schema)

    user_cols = table_columns(db, "users")
    if "is_admin" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "bio" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN bio TEXT NOT NULL DEFAULT ''")
    if "avatar" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN avatar TEXT")
    if "last_seen" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")

    post_cols = table_columns(db, "posts")
    if "image" not in post_cols:
        db.execute("ALTER TABLE posts ADD COLUMN image TEXT")
    if "updated_at" not in post_cols:
        db.execute("ALTER TABLE posts ADD COLUMN updated_at TEXT")
    if "category_id" not in post_cols:
        db.execute("ALTER TABLE posts ADD COLUMN category_id INTEGER REFERENCES categories (id)")

    cat_cols = table_columns(db, "categories")
    if "created_by" not in cat_cols:
        db.execute("ALTER TABLE categories ADD COLUMN created_by INTEGER REFERENCES users (id)")

    db.commit()
    remove_old_default_categories()
    backfill_site_owner_contacts()


def remove_old_default_categories():
    db = get_db()
    for name in OLD_DEFAULT_CATEGORIES:
        cat = db.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
        if cat:
            db.execute("UPDATE posts SET category_id = NULL WHERE category_id = ?", (cat["id"],))
            db.execute("DELETE FROM categories WHERE id = ?", (cat["id"],))
    db.commit()


def get_site_owner():
    db = get_db()
    return db.execute(
        "SELECT id, username FROM users WHERE username = ?", (SITE_OWNER,)
    ).fetchone()


def ensure_site_owner_contact(user_id):
    owner = get_site_owner()
    if not owner or owner["id"] == user_id:
        return
    get_or_create_conversation(user_id, owner["id"])


def backfill_site_owner_contacts():
    owner = get_site_owner()
    if not owner:
        return
    db = get_db()
    users = db.execute("SELECT id FROM users WHERE id != ?", (owner["id"],)).fetchall()
    for user in users:
        get_or_create_conversation(owner["id"], user["id"])


def init_db():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    migrate_db()
    migrate_disk_uploads_to_db()
    promote_admin_from_env()


def get_admin_usernames():
    names = []
    for key in ("ADMIN_USERNAMES", "ADMIN_USERNAME"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        for part in raw.split(","):
            name = part.strip()
            if name and name not in names:
                names.append(name)
    return names


def promote_admin_from_env():
    admin_usernames = get_admin_usernames()
    if not admin_usernames:
        return
    db = get_db()
    changed = False
    for admin_username in admin_usernames:
        user = db.execute(
            "SELECT id, is_admin FROM users WHERE username = ?", (admin_username,)
        ).fetchone()
        if user and not user["is_admin"]:
            db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
            changed = True
    if changed:
        db.commit()


def make_admin(username):
    with app.app_context():
        migrate_db()
        db = get_db()
        user = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not user:
            print(f"Пользователь '{username}' не найден.")
            return False
        db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
        db.commit()
        print(f"'{username}' теперь администратор.")
        return True


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def media_url(filename):
    if not filename:
        return ""
    return url_for("serve_media", filename=filename)


def save_media(filename, data, mime_type):
    db = get_db()
    db.execute(
        "INSERT INTO media (filename, data, mime_type, created_at) VALUES (?, ?, ?, ?)",
        (filename, data, mime_type, datetime.now().isoformat()),
    )


def save_upload(file):
    if not file or not file.filename:
        return None
    if not allowed_file(file.filename):
        return None
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    data = file.read()
    if not data:
        return None
    mime_type = MIME_TYPES.get(ext, "application/octet-stream")
    save_media(filename, data, mime_type)
    return filename


def delete_file(filename):
    if not filename or not MEDIA_FILENAME_RE.match(filename):
        return
    db = get_db()
    db.execute("DELETE FROM media WHERE filename = ?", (filename,))
    path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.isfile(path):
        os.remove(path)


def migrate_disk_uploads_to_db():
    if not os.path.isdir(UPLOAD_FOLDER):
        return
    db = get_db()
    migrated = 0
    for name in os.listdir(UPLOAD_FOLDER):
        if not MEDIA_FILENAME_RE.match(name):
            continue
        if db.execute("SELECT filename FROM media WHERE filename = ?", (name,)).fetchone():
            continue
        path = os.path.join(UPLOAD_FOLDER, name)
        if not os.path.isfile(path):
            continue
        ext = name.rsplit(".", 1)[1].lower()
        with open(path, "rb") as handle:
            data = handle.read()
        if not data:
            continue
        save_media(name, data, MIME_TYPES.get(ext, "application/octet-stream"))
        migrated += 1
    if migrated:
        db.commit()


def is_admin():
    return g.user and g.user["is_admin"]


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session or not g.user:
            session.pop("user_id", None)
            flash("Войдите, чтобы продолжить.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Войдите, чтобы продолжить.", "warning")
            return redirect(url_for("login"))
        if not is_admin():
            flash("Доступ только для администратора.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated


def get_categories():
    db = get_db()
    return db.execute("""
        SELECT categories.id, categories.name, categories.slug,
               categories.created_by,
               COUNT(posts.id) AS post_count
        FROM categories
        LEFT JOIN posts ON posts.category_id = categories.id
        GROUP BY categories.id
        ORDER BY categories.name
    """).fetchall()


def get_message_contacts(user_id, search=""):
    db = get_db()
    query = """
        SELECT u.id, u.username, u.avatar, u.last_seen,
               conv.id AS conv_id,
               (SELECT content FROM messages WHERE messages.conversation_id = conv.id
                ORDER BY messages.created_at DESC LIMIT 1) AS last_message,
               (SELECT created_at FROM messages WHERE messages.conversation_id = conv.id
                ORDER BY messages.created_at DESC LIMIT 1) AS last_message_at,
               (SELECT COUNT(*) FROM messages WHERE messages.conversation_id = conv.id
                AND messages.sender_id != ? AND messages.is_read = 0) AS unread_count
        FROM users u
        LEFT JOIN conversations conv ON (
            (conv.user1_id = ? AND conv.user2_id = u.id) OR
            (conv.user2_id = ? AND conv.user1_id = u.id)
        )
        WHERE u.id != ?
    """
    params = [user_id, user_id, user_id, user_id]
    if search:
        query += " AND u.username LIKE ?"
        params.append(f"%{search}%")
    query += """
        ORDER BY (
            SELECT MAX(messages.created_at)
            FROM messages
            WHERE messages.conversation_id = conv.id
        ) DESC NULLS LAST, u.username ASC
    """
    return db.execute(query, params).fetchall()


def get_popular_tags(limit=20):
    db = get_db()
    return db.execute("""
        SELECT tags.id, tags.name, tags.slug,
               COUNT(post_tags.post_id) AS post_count
        FROM tags
        LEFT JOIN post_tags ON post_tags.tag_id = tags.id
        GROUP BY tags.id
        HAVING COUNT(post_tags.post_id) > 0
        ORDER BY post_count DESC, tags.name
        LIMIT ?
    """, (limit,)).fetchall()


def get_post_tags(post_id):
    db = get_db()
    return db.execute("""
        SELECT tags.id, tags.name, tags.slug
        FROM tags
        JOIN post_tags ON post_tags.tag_id = tags.id
        WHERE post_tags.post_id = ?
        ORDER BY tags.name
    """, (post_id,)).fetchall()


def attach_tags_to_posts(posts):
    result = []
    for post in posts:
        post_dict = dict(post)
        post_dict["tags"] = get_post_tags(post["id"])
        result.append(post_dict)
    return result


def parse_tags_input(raw):
    names = []
    for part in raw.split(","):
        name = normalize_tag_name(part)
        if name and name not in names:
            names.append(name)
    return names[:10]


def get_or_create_tag(name):
    name = normalize_tag_name(name)
    if not name:
        return None
    db = get_db()
    slug = slugify(name)

    def find_tag():
        return db.execute(
            "SELECT id FROM tags WHERE slug = ? OR name = ? OR name = ?",
            (slug, name, f"#{name}"),
        ).fetchone()

    existing = find_tag()
    if existing:
        return existing["id"]
    now = datetime.now().isoformat()
    cur = db.execute(
        "INSERT INTO tags (name, slug, created_at) VALUES (?, ?, ?)",
        (name, slug, now),
    )
    if cur.lastrowid:
        return cur.lastrowid
    existing = find_tag()
    return existing["id"] if existing else None


def set_post_tags(post_id, tag_names):
    db = get_db()
    db.execute("DELETE FROM post_tags WHERE post_id = ?", (post_id,))
    for name in tag_names:
        tag_id = get_or_create_tag(name)
        if tag_id:
            db.execute(
                "INSERT OR IGNORE INTO post_tags (post_id, tag_id) VALUES (?, ?)",
                (post_id, tag_id),
            )


def fetch_posts(category_slug=None, tag_slug=None):
    db = get_db()
    query = """
        SELECT posts.id, posts.title, posts.content, posts.image, posts.created_at,
               users.username, users.id AS author_id, users.last_seen AS author_last_seen,
               categories.name AS category_name, categories.slug AS category_slug,
               (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id) AS comment_count
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
    """
    params = []
    conditions = []

    if category_slug:
        conditions.append("categories.slug = ?")
        params.append(category_slug)
    if tag_slug:
        query += " JOIN post_tags ON post_tags.post_id = posts.id JOIN tags ON tags.id = post_tags.tag_id"
        conditions.append("tags.slug = ?")
        params.append(tag_slug)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY posts.created_at DESC"
    return db.execute(query, params).fetchall()


def get_unread_count(user_id):
    db = get_db()
    return db.execute("""
        SELECT COUNT(*) FROM messages
        JOIN conversations ON conversations.id = messages.conversation_id
        WHERE messages.sender_id != ?
          AND messages.is_read = 0
          AND (conversations.user1_id = ? OR conversations.user2_id = ?)
    """, (user_id, user_id, user_id)).fetchone()[0]


def get_or_create_conversation(user_id, other_user_id):
    if user_id == other_user_id:
        return None
    a, b = min(user_id, other_user_id), max(user_id, other_user_id)
    db = get_db()

    def find_conversation():
        return db.execute(
            "SELECT id FROM conversations WHERE user1_id = ? AND user2_id = ?",
            (a, b),
        ).fetchone()

    conv = find_conversation()
    if conv:
        return conv["id"]
    now = datetime.now().isoformat()
    try:
        cur = db.execute(
            "INSERT INTO conversations (user1_id, user2_id, created_at) VALUES (?, ?, ?)",
            (a, b, now),
        )
        db.commit()
        if cur.lastrowid:
            return cur.lastrowid
    except Exception:
        app.logger.exception("conversation insert failed for %s %s", a, b)
        try:
            db._conn.rollback()
        except Exception:
            pass
    conv = find_conversation()
    return conv["id"] if conv else None


def user_in_conversation(conv_id, user_id):
    db = get_db()
    conv = db.execute(
        "SELECT id FROM conversations WHERE id = ? AND (user1_id = ? OR user2_id = ?)",
        (conv_id, user_id, user_id),
    ).fetchone()
    return conv is not None


def get_conversation_partner(conv_id, user_id):
    db = get_db()
    conv = db.execute(
        "SELECT user1_id, user2_id FROM conversations WHERE id = ?",
        (conv_id,),
    ).fetchone()
    if not conv:
        return None
    partner_id = conv["user2_id"] if conv["user1_id"] == user_id else conv["user1_id"]
    return db.execute(
        "SELECT id, username, avatar FROM users WHERE id = ?",
        (partner_id,),
    ).fetchone()


@app.route("/media/<filename>")
def serve_media(filename):
    if not MEDIA_FILENAME_RE.match(filename):
        abort(404)
    db = get_db()
    row = db.execute(
        "SELECT data, mime_type FROM media WHERE filename = ?",
        (filename,),
    ).fetchone()
    if not row:
        path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.isfile(path):
            ext = filename.rsplit(".", 1)[1].lower()
            with open(path, "rb") as handle:
                data = handle.read()
            mime_type = MIME_TYPES.get(ext, "application/octet-stream")
            save_media(filename, data, mime_type)
            db.commit()
            return Response(data, mimetype=mime_type, headers={"Cache-Control": "public, max-age=31536000, immutable"})
        abort(404)
    data = row["data"]
    if isinstance(data, memoryview):
        data = data.tobytes()
    return Response(
        data,
        mimetype=row["mime_type"],
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.route("/health")
def health():
    return jsonify({
        "ok": database.USE_POSTGRES or not IS_RENDER,
        "database": "postgresql" if database.USE_POSTGRES else "sqlite",
        "persistent": database.IS_PERSISTENT or not IS_RENDER,
        "render": IS_RENDER,
        "database_url_set": bool(database.DATABASE_URL),
    })


@app.before_request
def before_request():
    if IS_RENDER and not database.USE_POSTGRES:
        if request.endpoint in ("health", "static"):
            return None
        return render_template("db_setup_required.html"), 503

    path = request.path
    if path != "/" and path.endswith("/"):
        target = path.rstrip("/")
        if request.query_string:
            target += "?" + request.query_string.decode()
        return redirect(target)

    g.user = None
    g.unread_messages = 0
    if "user_id" in session:
        try:
            db = get_db()
            g.user = db.execute(
                "SELECT id, username, email, is_admin, bio, avatar, last_seen FROM users WHERE id = ?",
                (session["user_id"],),
            ).fetchone()
            if g.user:
                touch_last_seen(g.user["id"])
                g.unread_messages = get_unread_count(g.user["id"])
        except Exception:
            app.logger.exception("before_request failed for user_id=%s", session.get("user_id"))
            g.user = None
            g.unread_messages = 0
            try:
                get_db()._conn.rollback()
            except Exception:
                pass


@app.context_processor
def inject_globals():
    return {
        "is_admin": is_admin(),
        "unread_messages": getattr(g, "unread_messages", 0),
        "site_url": app.config["SITE_URL"],
        "public_url": app.config["PUBLIC_URL"],
        "site_name": SITE_NAME,
        "media_url": media_url,
        "db_persistent": database.IS_PERSISTENT or not IS_RENDER,
    }


def render_posts_page(category_slug=None, tag_slug=None):
    categories = get_categories()
    tags = get_popular_tags()
    posts_raw = fetch_posts(category_slug=category_slug, tag_slug=tag_slug)
    posts = attach_tags_to_posts(posts_raw)

    page_title = "Последние посты"
    page_subtitle = None
    show_user_online = not category_slug and not tag_slug
    active_category = None
    active_tag = None

    if category_slug:
        active_category = next((c for c in categories if c["slug"] == category_slug), None)
        if active_category:
            page_title = f"Категория: {active_category['name']}"
            page_subtitle = f"{active_category['post_count']} постов в категории"
    if tag_slug:
        db = get_db()
        active_tag = db.execute(
            "SELECT id, name, slug FROM tags WHERE slug = ?", (tag_slug,)
        ).fetchone()
        if active_tag:
            page_title = f"Тег: #{tag_display(active_tag['name'])}"
            page_subtitle = f"{len(posts)} постов с этим тегом"

    return render_template(
        "index.html",
        posts=posts,
        categories=categories,
        tags=tags,
        page_title=page_title,
        page_subtitle=page_subtitle,
        show_user_online=show_user_online,
        active_category=active_category,
        active_tag=active_tag,
    )


@app.route("/")
def index():
    return render_posts_page()


@app.route("/category/<slug>")
def category_posts(slug):
    db = get_db()
    category = db.execute("SELECT id FROM categories WHERE slug = ?", (slug,)).fetchone()
    if not category:
        flash("Категория не найдена.", "error")
        return redirect(url_for("index"))
    return render_posts_page(category_slug=slug)


@app.route("/tag/<slug>")
def tag_posts(slug):
    db = get_db()
    tag = db.execute("SELECT id FROM tags WHERE slug = ?", (slug,)).fetchone()
    if not tag:
        flash("Тег не найден.", "error")
        return redirect(url_for("index"))
    return render_posts_page(tag_slug=slug)


@app.route("/categories/create", methods=["POST"])
@admin_required
def create_category():
    name = request.form.get("name", "").strip()
    redirect_to = request.form.get("next") or request.referrer or url_for("index")

    if len(name) < 2:
        flash("Название категории — минимум 2 символа.", "error")
        return redirect(redirect_to)
    if len(name) > 40:
        flash("Название категории — максимум 40 символов.", "error")
        return redirect(redirect_to)

    db = get_db()
    slug = slugify(name)
    existing = db.execute(
        "SELECT id FROM categories WHERE name = ? OR slug = ?", (name, slug)
    ).fetchone()
    if existing:
        flash("Категория с таким названием уже существует.", "error")
        return redirect(redirect_to)

    db.execute(
        "INSERT INTO categories (name, slug, created_at, created_by) VALUES (?, ?, ?, ?)",
        (name, slug, datetime.now().isoformat(), session["user_id"]),
    )
    db.commit()
    flash(f"Категория «{name}» создана!", "success")
    return redirect(redirect_to)


@app.route("/categories/<int:cat_id>/delete", methods=["POST"])
@admin_required
def delete_category(cat_id):
    db = get_db()
    category = db.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
    redirect_to = request.form.get("next") or request.referrer or url_for("index")

    if not category:
        flash("Категория не найдена.", "error")
        return redirect(redirect_to)

    post_count = db.execute(
        "SELECT COUNT(*) FROM posts WHERE category_id = ?", (cat_id,)
    ).fetchone()[0]

    if post_count > 0:
        flash("Нельзя удалить категорию с постами.", "error")
        return redirect(redirect_to)

    db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    db.commit()
    flash(f"Категория «{category['name']}» удалена.", "info")
    return redirect(redirect_to)


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = []
        if len(username) < 3:
            errors.append("Имя пользователя — минимум 3 символа.")
        if "@" not in email or len(email) < 5:
            errors.append("Введите корректный email.")
        if len(password) < 6:
            errors.append("Пароль — минимум 6 символов.")
        if password != confirm:
            errors.append("Пароли не совпадают.")

        if not errors:
            db = get_db()
            existing = db.execute(
                "SELECT id FROM users WHERE username = ? OR email = ?",
                (username, email),
            ).fetchone()
            if existing:
                errors.append("Пользователь с таким именем или email уже существует.")
            else:
                is_new_admin = 1 if username in get_admin_usernames() else 0
                cur = db.execute(
                    """INSERT INTO users (username, email, password_hash, created_at, is_admin)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        username,
                        email,
                        generate_password_hash(password),
                        datetime.now().isoformat(),
                        is_new_admin,
                    ),
                )
                new_user_id = cur.lastrowid
                db.commit()
                ensure_site_owner_contact(new_user_id)
                session.clear()
                session["user_id"] = new_user_id
                flash(f"Добро пожаловать, {username}! Вы зарегистрированы.", "success")
                return redirect(url_for("index"))

        for error in errors:
            flash(error, "error")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))

    if request.method == "POST":
        login_input = request.form.get("login", "").strip()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (login_input, login_input.lower()),
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Добро пожаловать, {user['username']}!", "success")
            return redirect(url_for("index"))

        flash("Неверное имя пользователя или пароль.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for("index"))


@app.route("/user/<username>")
def user_profile(username):
    db = get_db()
    profile_user = db.execute(
        "SELECT id, username, bio, avatar, created_at, is_admin, last_seen FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if not profile_user:
        flash("Пользователь не найден.", "error")
        return redirect(url_for("index"))

    posts_raw = db.execute("""
        SELECT posts.id, posts.title, posts.content, posts.image, posts.created_at,
               categories.name AS category_name, categories.slug AS category_slug,
               (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id) AS comment_count
        FROM posts
        LEFT JOIN categories ON posts.category_id = categories.id
        WHERE posts.user_id = ?
        ORDER BY posts.created_at DESC
    """, (profile_user["id"],)).fetchall()
    posts = attach_tags_to_posts(posts_raw)

    return render_template(
        "profile.html",
        profile_user=profile_user,
        posts=posts,
        post_count=len(posts),
    )


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()
        if len(bio) > 500:
            flash("О себе — максимум 500 символов.", "error")
            return render_template("edit_profile.html")

        db = get_db()
        avatar = g.user["avatar"]
        if "avatar" in request.files:
            file = request.files["avatar"]
            saved = save_upload(file)
            if file and file.filename and not saved:
                flash("Допустимые форматы аватара: PNG, JPG, GIF, WEBP.", "error")
                return render_template("edit_profile.html")
            if saved:
                delete_file(avatar)
                avatar = saved

        db.execute(
            "UPDATE users SET bio = ?, avatar = ? WHERE id = ?",
            (bio, avatar, session["user_id"]),
        )
        db.commit()
        flash("Профиль обновлён!", "success")
        return redirect(url_for("user_profile", username=g.user["username"]))

    return render_template("edit_profile.html")


def post_form_context(post=None):
    db = get_db()
    categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    selected_category = post["category_id"] if post else None
    selected_tags = ""
    if post:
        tags = get_post_tags(post["id"])
        selected_tags = ", ".join(tag_display(t["name"]) for t in tags)
    return {
        "categories": categories,
        "selected_category": selected_category,
        "selected_tags": selected_tags,
    }


@app.route("/post/new", methods=["GET", "POST"])
@login_required
def create_post():
    try:
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            category_id = request.form.get("category_id", type=int)
            tags_raw = request.form.get("tags", "")
            image = save_upload(request.files.get("image"))

            if request.files.get("image") and request.files["image"].filename and not image:
                flash("Допустимые форматы изображения: PNG, JPG, GIF, WEBP.", "error")
                return render_template("create_post.html", **post_form_context())

            errors = []
            if len(title) < 3:
                errors.append("Заголовок — минимум 3 символа.")
            if len(content) < 10:
                errors.append("Текст поста — минимум 10 символов.")
            if not category_id:
                errors.append("Выберите категорию.")

            if not errors:
                db = get_db()
                cat = db.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
                if not cat:
                    errors.append("Категория не найдена.")

            if not errors:
                now = datetime.now().isoformat()
                db = get_db()
                cur = db.execute(
                    """INSERT INTO posts (user_id, title, content, image, category_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (session["user_id"], title, content, image, category_id, now, now),
                )
                post_id = cur.lastrowid
                if not post_id:
                    raise RuntimeError("post insert did not return id")
                set_post_tags(post_id, parse_tags_input(tags_raw))
                db.commit()
                flash("Пост опубликован!", "success")
                return redirect(url_for("view_post", post_id=post_id))

            for error in errors:
                flash(error, "error")

        return render_template("create_post.html", **post_form_context())
    except Exception:
        app.logger.exception("create_post failed")
        try:
            get_db()._conn.rollback()
        except Exception:
            pass
        flash("Не удалось создать пост. Попробуйте ещё раз без изображения.", "error")
        return render_template("create_post.html", **post_form_context()), 500


@app.route("/post/<int:post_id>")
def view_post(post_id):
    db = get_db()
    post = db.execute("""
        SELECT posts.id, posts.title, posts.content, posts.image,
               posts.created_at, posts.updated_at, posts.category_id,
               users.username, users.id AS author_id,
               categories.name AS category_name, categories.slug AS category_slug
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
        WHERE posts.id = ?
    """, (post_id,)).fetchone()

    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))

    comments = db.execute("""
        SELECT comments.id, comments.content, comments.created_at,
               users.username, users.id AS author_id
        FROM comments
        JOIN users ON comments.user_id = users.id
        WHERE comments.post_id = ?
        ORDER BY comments.created_at ASC
    """, (post_id,)).fetchall()

    post_tags = get_post_tags(post_id)

    return render_template("post.html", post=post, comments=comments, post_tags=post_tags)


@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()

    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))

    if post["user_id"] != session["user_id"] and not is_admin():
        flash("Вы не можете редактировать чужой пост.", "error")
        return redirect(url_for("view_post", post_id=post_id))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        category_id = request.form.get("category_id", type=int)
        tags_raw = request.form.get("tags", "")
        remove_image = request.form.get("remove_image") == "on"
        new_image = save_upload(request.files.get("image"))
        image = post["image"]

        if request.files.get("image") and request.files["image"].filename and not new_image:
            flash("Допустимые форматы изображения: PNG, JPG, GIF, WEBP.", "error")
            return render_template("edit_post.html", post=post, **post_form_context(post))

        if remove_image:
            delete_file(image)
            image = None
        elif new_image:
            delete_file(image)
            image = new_image

        errors = []
        if len(title) < 3:
            errors.append("Заголовок — минимум 3 символа.")
        if len(content) < 10:
            errors.append("Текст поста — минимум 10 символов.")
        if not category_id:
            errors.append("Выберите категорию.")

        if not errors:
            cat = db.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
            if not cat:
                errors.append("Категория не найдена.")

        if not errors:
            db.execute(
                """UPDATE posts SET title = ?, content = ?, image = ?, category_id = ?, updated_at = ?
                   WHERE id = ?""",
                (title, content, image, category_id, datetime.now().isoformat(), post_id),
            )
            set_post_tags(post_id, parse_tags_input(tags_raw))
            db.commit()
            flash("Пост обновлён!", "success")
            return redirect(url_for("view_post", post_id=post_id))

        for error in errors:
            flash(error, "error")

    return render_template("edit_post.html", post=post, **post_form_context(post))


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()

    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))

    if post["user_id"] != session["user_id"] and not is_admin():
        flash("Вы не можете удалить чужой пост.", "error")
        return redirect(url_for("view_post", post_id=post_id))

    delete_file(post["image"])
    db.execute("DELETE FROM post_tags WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    db.commit()
    flash("Пост удалён.", "info")
    if is_admin() and request.referrer and "/admin" in request.referrer:
        return redirect(url_for("admin_panel"))
    return redirect(url_for("index"))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    db = get_db()
    post = db.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))

    content = request.form.get("content", "").strip()
    if len(content) < 2:
        flash("Комментарий слишком короткий.", "error")
        return redirect(url_for("view_post", post_id=post_id))

    db.execute(
        "INSERT INTO comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
        (post_id, session["user_id"], content, datetime.now().isoformat()),
    )
    db.commit()
    flash("Комментарий добавлен!", "success")
    return redirect(url_for("view_post", post_id=post_id))


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    db = get_db()
    comment = db.execute(
        "SELECT * FROM comments WHERE id = ?", (comment_id,)
    ).fetchone()

    if not comment:
        flash("Комментарий не найден.", "error")
        return redirect(url_for("index"))

    if comment["user_id"] != session["user_id"] and not is_admin():
        abort(403)

    post_id = comment["post_id"]
    db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    db.commit()
    flash("Комментарий удалён.", "info")
    return redirect(url_for("view_post", post_id=post_id))


@app.route("/messages")
@login_required
def messages_inbox():
    try:
        search = request.args.get("q", "").strip()
        contacts = get_message_contacts(g.user["id"], search)
        return render_template("messages.html", contacts=contacts, search=search)
    except Exception:
        app.logger.exception("messages_inbox failed")
        flash("Не удалось загрузить сообщения. Попробуйте ещё раз.", "error")
        return redirect(url_for("index"))


@app.route("/api/heartbeat")
@login_required
def api_heartbeat():
    touch_last_seen(session["user_id"])
    return jsonify({"ok": True})


@app.route("/api/users/search")
@login_required
def api_users_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    db = get_db()
    rows = db.execute(
        """SELECT id, username, avatar FROM users
           WHERE id != ? AND username LIKE ?
           ORDER BY username LIMIT 20""",
        (session["user_id"], f"%{query}%"),
    ).fetchall()
    return jsonify([{"username": r["username"], "avatar": r["avatar"]} for r in rows])


@app.route("/messages/<username>", methods=["GET", "POST"])
@login_required
def chat_with_user(username):
    try:
        return _chat_with_user(username)
    except Exception:
        app.logger.exception("chat_with_user failed for %s", username)
        flash("Не удалось открыть чат. Попробуйте ещё раз.", "error")
        return redirect(url_for("messages_inbox"))


def _chat_with_user(username):
    db = get_db()
    partner = db.execute(
        "SELECT id, username, avatar, last_seen FROM users WHERE username = ?", (username,)
    ).fetchone()

    if not partner:
        flash("Пользователь не найден.", "error")
        return redirect(url_for("messages_inbox"))

    if partner["id"] == g.user["id"]:
        flash("Нельзя написать самому себе.", "warning")
        return redirect(url_for("messages_inbox"))

    conv_id = get_or_create_conversation(g.user["id"], partner["id"])
    if not conv_id:
        flash("Не удалось создать диалог.", "error")
        return redirect(url_for("messages_inbox"))

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if len(content) < 1:
            flash("Сообщение не может быть пустым.", "error")
        elif len(content) > 2000:
            flash("Сообщение слишком длинное (макс. 2000 символов).", "error")
        else:
            db.execute(
                "INSERT INTO messages (conversation_id, sender_id, content, created_at) VALUES (?, ?, ?, ?)",
                (conv_id, g.user["id"], content, datetime.now().isoformat()),
            )
            db.commit()
            return redirect(url_for("chat_with_user", username=username))

    db.execute("""
        UPDATE messages SET is_read = 1
        WHERE conversation_id = ? AND sender_id != ? AND is_read = 0
    """, (conv_id, g.user["id"]))
    db.commit()

    messages = db.execute("""
        SELECT messages.id, messages.content, messages.created_at, messages.sender_id,
               users.username AS sender_name
        FROM messages
        JOIN users ON users.id = messages.sender_id
        WHERE messages.conversation_id = ?
        ORDER BY messages.created_at ASC
    """, (conv_id,)).fetchall()

    contacts = get_message_contacts(g.user["id"])

    return render_template(
        "chat.html",
        partner=partner,
        messages=messages,
        conv_id=conv_id,
        contacts=contacts,
    )


@app.route("/api/chat/<int:conv_id>/messages")
@login_required
def api_chat_messages(conv_id):
    if not user_in_conversation(conv_id, session["user_id"]):
        abort(403)

    after_id = request.args.get("after", 0, type=int)
    db = get_db()

    db.execute("""
        UPDATE messages SET is_read = 1
        WHERE conversation_id = ? AND sender_id != ? AND is_read = 0
    """, (conv_id, session["user_id"]))

    rows = db.execute("""
        SELECT messages.id, messages.content, messages.created_at, messages.sender_id,
               users.username AS sender_name
        FROM messages
        JOIN users ON users.id = messages.sender_id
        WHERE messages.conversation_id = ? AND messages.id > ?
        ORDER BY messages.created_at ASC
    """, (conv_id, after_id)).fetchall()

    db.commit()

    touch_last_seen(session["user_id"])

    conv = db.execute(
        "SELECT user1_id, user2_id FROM conversations WHERE id = ?",
        (conv_id,),
    ).fetchone()
    partner_id = None
    if conv:
        partner_id = (
            conv["user2_id"]
            if conv["user1_id"] == session["user_id"]
            else conv["user1_id"]
        )
    partner_online = False
    if partner_id:
        partner = db.execute(
            "SELECT last_seen FROM users WHERE id = ?",
            (partner_id,),
        ).fetchone()
        if partner:
            partner_online = is_user_online(partner["last_seen"])

    return jsonify({
        "messages": [
            {
                "id": r["id"],
                "content": r["content"],
                "created_at": r["created_at"][:16].replace("T", " "),
                "sender_id": r["sender_id"],
                "sender_name": r["sender_name"],
                "is_mine": r["sender_id"] == session["user_id"],
            }
            for r in rows
        ],
        "partner_online": partner_online,
    })


@app.route("/admin")
@admin_required
def admin_panel():
    db = get_db()
    posts = db.execute("""
        SELECT posts.id, posts.title, posts.created_at,
               users.username, users.id AS author_id,
               categories.name AS category_name
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
        ORDER BY posts.created_at DESC
    """).fetchall()

    users = db.execute("""
        SELECT id, username, email, is_admin, created_at,
               (SELECT COUNT(*) FROM posts WHERE posts.user_id = users.id) AS post_count
        FROM users
        ORDER BY users.created_at DESC
    """).fetchall()

    stats = {
        "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "posts": db.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "comments": db.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
        "messages": db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
    }

    return render_template("admin.html", posts=posts, users=users, stats=stats)


@app.errorhandler(404)
def not_found(_error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(_error):
    app.logger.exception("internal server error")
    return render_template("500.html"), 500


with app.app_context():
    init_db()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "make-admin":
        make_admin(sys.argv[2])
        sys.exit(0)

    app.run(debug=True, host="0.0.0.0", port=5000)
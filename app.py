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
SITE_NAME = os.environ.get("SITE_NAME", "Приватный форум")
SITE_BYLINE = os.environ.get("SITE_BYLINE", "by sozeracke")
ONLINE_THRESHOLD_SECONDS = 300

USER_LEVELS = [
    {"min_xp": 0, "name": "Новичок", "icon": "🥉"},
    {"min_xp": 100, "name": "Активный", "icon": "🥈"},
    {"min_xp": 300, "name": "Эксперт", "icon": "🥇"},
    {"min_xp": 700, "name": "VIP", "icon": "💎"},
    {"min_xp": 1500, "name": "Основатель", "icon": "👑"},
]

XP_REWARD = {
    "register": 10,
    "message": 2,
    "comment": 5,
    "post": 15,
    "proposal": 5,
    "like": 1,
    "liked": 3,
}

PROPOSAL_STATUS_PENDING = "pending"
PROPOSAL_STATUS_APPROVED = "approved"
PROPOSAL_STATUS_REJECTED = "rejected"
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

IMAGE_PLACEHOLDER = "[[IMAGE]]"
INLINE_IMG_MARKER_RE = re.compile(
    r"\[img:([a-f0-9]+\.(?:png|jpg|jpeg|gif|webp))\]", re.IGNORECASE
)


def save_uploads(files):
    names = []
    for file in files or []:
        filename = save_upload(file)
        if filename:
            names.append(filename)
    return names


def embed_content_images(content, files):
    uploaded = save_uploads(files)
    if not uploaded:
        return content
    if IMAGE_PLACEHOLDER in content:
        result = content
        for filename in uploaded:
            result = result.replace(IMAGE_PLACEHOLDER, f"[img:{filename}]", 1)
        return result
    markers = "\n\n".join(f"[img:{filename}]" for filename in uploaded)
    content = (content or "").rstrip()
    return f"{content}\n\n{markers}" if content else markers


def strip_inline_image_markers(content):
    if not content:
        return ""
    cleaned = INLINE_IMG_MARKER_RE.sub("", content)
    cleaned = cleaned.replace(IMAGE_PLACEHOLDER, "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def extract_inline_images(content):
    if not content:
        return []
    return list(dict.fromkeys(INLINE_IMG_MARKER_RE.findall(content)))


def render_post_content(text):
    if not text:
        return Markup("")
    parts = INLINE_IMG_MARKER_RE.split(str(text))
    chunks = []
    for index, part in enumerate(parts):
        if index % 2 == 0:
            if part:
                chunks.append(str(linkify(part)))
            continue
        if MEDIA_FILENAME_RE.match(part):
            url = escape(media_url(part))
            chunks.append(
                f'<figure class="post-inline-figure">'
                f'<img src="{url}" alt="" class="post-inline-image lightbox-image" '
                f'loading="lazy" tabindex="0" role="button">'
                f"</figure>"
            )
    return Markup("".join(chunks))


def delete_inline_images_from_content(content):
    for filename in extract_inline_images(content):
        delete_file(filename)


app.jinja_env.filters["render_post_content"] = render_post_content
app.jinja_env.filters["post_excerpt"] = strip_inline_image_markers


def post_cover_filename(post):
    if post.get("image"):
        return post["image"]
    inline = extract_inline_images(post.get("content", ""))
    return inline[0] if inline else None


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")[:19])
    except ValueError:
        return None


def format_date_ru(value):
    dt = parse_iso_datetime(value)
    if not dt:
        return (value or "")[:10]
    days = (datetime.now() - dt).days
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days < 7:
        return f"{days} дн. назад"
    return dt.strftime("%d.%m.%Y")


def reading_time(text):
    clean = strip_inline_image_markers(text or "")
    words = len(re.findall(r"\S+", clean))
    return max(1, round(words / 200)) if words else 1


def absolute_media_url(filename):
    if not filename:
        return ""
    base = app.config["PUBLIC_URL"].rstrip("/")
    return f"{base}{url_for('serve_media', filename=filename)}"


app.jinja_env.filters["format_date_ru"] = format_date_ru
app.jinja_env.filters["reading_time"] = reading_time


def normalize_tag_name(name):
    name = name.strip()
    while name.startswith("#"):
        name = name[1:].strip()
    return name[:40]


def tag_display(name):
    return normalize_tag_name(name)


app.jinja_env.filters["tag_display"] = tag_display


def avatar_letter(username):
    if not username:
        return "?"
    name = str(username).strip()
    return name[0].upper() if name else "?"


def avatar_bg_variant(username, user_id=None):
    if user_id is not None:
        seed = int(user_id)
    elif username:
        seed = sum(ord(ch) for ch in str(username))
    else:
        seed = 0
    return "alt" if seed % 2 else "default"


app.jinja_env.filters["avatar_letter"] = avatar_letter
app.jinja_env.filters["avatar_bg_variant"] = avatar_bg_variant


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
    if "xp" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")

    post_cols = table_columns(db, "posts")
    if "image" not in post_cols:
        db.execute("ALTER TABLE posts ADD COLUMN image TEXT")
    if "updated_at" not in post_cols:
        db.execute("ALTER TABLE posts ADD COLUMN updated_at TEXT")
    if "category_id" not in post_cols:
        db.execute("ALTER TABLE posts ADD COLUMN category_id INTEGER REFERENCES categories (id)")
    if "published_at" not in post_cols:
        db.execute("ALTER TABLE posts ADD COLUMN published_at TEXT")
        db.execute(
            "UPDATE posts SET published_at = created_at WHERE published_at IS NULL"
        )
    if "is_private" not in post_cols:
        db.execute(
            "ALTER TABLE posts ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0"
        )
    if "is_pinned" not in post_cols:
        db.execute(
            "ALTER TABLE posts ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0"
        )

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


def publication_cutoff():
    return datetime.now().isoformat()


def current_user_id():
    user = getattr(g, "user", None)
    return user["id"] if user else None


def award_xp(user_id, amount):
    if not user_id or amount <= 0:
        return
    db = get_db()
    db.execute("UPDATE users SET xp = xp + ? WHERE id = ?", (amount, user_id))


def get_user_level_info(xp, username=None, is_admin=False):
    try:
        xp = int(xp or 0)
    except (TypeError, ValueError):
        xp = 0
    if username == SITE_OWNER:
        top = USER_LEVELS[-1]
        return {
            **top,
            "xp": xp,
            "next_xp": None,
            "progress": 100,
        }
    current = USER_LEVELS[0]
    nxt = None
    for level in USER_LEVELS:
        if xp >= level["min_xp"]:
            current = level
        else:
            nxt = level
            break
    if nxt:
        span = nxt["min_xp"] - current["min_xp"]
        gained = xp - current["min_xp"]
        progress = int(min(100, max(0, gained / span * 100))) if span else 100
        next_xp = nxt["min_xp"]
    else:
        progress = 100
        next_xp = None
    return {**current, "xp": xp, "next_xp": next_xp, "progress": progress}


def post_engagement_sql():
    user_id = current_user_id()
    liked_sql = (
        "EXISTS(SELECT 1 FROM post_likes pl "
        "WHERE pl.post_id = posts.id AND pl.user_id = ?) AS user_liked"
    )
    if user_id:
        return (
            """
               (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id)
                   AS comment_count,
               (SELECT COUNT(*) FROM post_likes WHERE post_likes.post_id = posts.id)
                   AS like_count,
            """
            + liked_sql
        ), [user_id]
    return """
               (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id)
                   AS comment_count,
               (SELECT COUNT(*) FROM post_likes WHERE post_likes.post_id = posts.id)
                   AS like_count,
               0 AS user_liked
        """, []


def post_publish_time(post):
    return post.get("published_at") or post["created_at"]


def is_post_published(post, now=None):
    now = now or publication_cutoff()
    return post_publish_time(post) <= now


def is_post_scheduled(post, now=None):
    now = now or publication_cutoff()
    published_at = post.get("published_at")
    return bool(published_at and published_at > now)


def post_is_public_sql(table_alias="posts"):
    return (
        f"COALESCE({table_alias}.published_at, {table_alias}.created_at) <= ?"
    )


def is_post_private(post):
    return bool(post.get("is_private"))


def is_post_pinned(post):
    return bool(post.get("is_pinned"))


def user_has_post_access(post_id, user_id):
    if not user_id:
        return False
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM post_access WHERE post_id = ? AND user_id = ?",
        (post_id, user_id),
    ).fetchone()
    return row is not None


def build_post_access_filter(user=None):
    if user and user.get("is_admin"):
        return "1=1", []
    user_id = user["id"] if user else None
    if user_id:
        return """(
            posts.is_private = 0
            OR posts.user_id = ?
            OR posts.id IN (SELECT post_id FROM post_access WHERE user_id = ?)
        )""", [user_id, user_id]
    return "posts.is_private = 0", []


def can_view_post(post, user=None):
    user = user if user is not None else getattr(g, "user", None)
    user_id = user["id"] if user else None
    author_id = post.get("author_id") or post.get("user_id")

    if user_id and author_id and user_id == author_id:
        return True
    if user and user.get("is_admin"):
        return True

    if not is_post_published(post):
        return False

    if not is_post_private(post):
        return True

    return user_id and user_has_post_access(post["id"], user_id)


def can_manage_post_access(post, user=None):
    user = user if user is not None else getattr(g, "user", None)
    if not user:
        return False
    if user.get("is_admin"):
        return True
    author_id = post.get("author_id") or post.get("user_id")
    return bool(author_id and user["id"] == author_id)


def post_access_back_url(post):
    if g.user and g.user.get("is_admin"):
        return url_for("admin_panel")
    return url_for("view_post", post_id=post["id"])


def get_post_access_users(post_id):
    db = get_db()
    return db.execute("""
        SELECT users.id, users.username, post_access.granted_at
        FROM post_access
        JOIN users ON users.id = post_access.user_id
        WHERE post_access.post_id = ?
        ORDER BY users.username
    """, (post_id,)).fetchall()


def format_datetime_local(iso_str):
    if not iso_str:
        return ""
    return iso_str[:16]


def parse_publish_schedule(form, default_now=None):
    default_now = default_now or datetime.now()
    if form.get("schedule_post") != "on":
        return default_now.isoformat(), False, None

    raw = form.get("published_at", "").strip()
    if not raw:
        return None, True, "Укажите дату и время публикации."
    try:
        scheduled = datetime.fromisoformat(raw)
    except ValueError:
        return None, True, "Некорректная дата публикации."
    if scheduled <= default_now:
        return None, True, "Время публикации должно быть в будущем."
    return scheduled.isoformat(), True, None


def resolve_edit_publish_at(form, post):
    if form.get("schedule_post") == "on":
        return parse_publish_schedule(form)
    if is_post_published(post):
        return post.get("published_at") or post["created_at"], False, None
    return datetime.now().isoformat(), False, None


def get_categories():
    db = get_db()
    cutoff = publication_cutoff()
    return db.execute(f"""
        SELECT categories.id, categories.name, categories.slug,
               categories.created_by,
               COUNT(posts.id) AS post_count
        FROM categories
        LEFT JOIN posts ON posts.category_id = categories.id
            AND posts.is_private = 0
            AND {post_is_public_sql("posts")}
        GROUP BY categories.id
        ORDER BY categories.name
    """, (cutoff,)).fetchall()


def get_message_contacts(user_id, search="", admins_only=False, admin_pins=False):
    db = get_db()
    pin_field = """
               , EXISTS (
                   SELECT 1 FROM pinned_conversations pc
                   WHERE pc.user_id = ? AND pc.conversation_id = conv.id
               ) AS is_pinned
    """ if admin_pins else ", 0 AS is_pinned"

    query = f"""
        SELECT u.id, u.username, u.avatar, u.last_seen, u.is_admin, u.xp,
               conv.id AS conv_id,
               (SELECT content FROM messages WHERE messages.conversation_id = conv.id
                ORDER BY messages.created_at DESC LIMIT 1) AS last_message,
               (SELECT created_at FROM messages WHERE messages.conversation_id = conv.id
                ORDER BY messages.created_at DESC LIMIT 1) AS last_message_at,
               (SELECT COUNT(*) FROM messages WHERE messages.conversation_id = conv.id
                AND messages.sender_id != ? AND messages.is_read = 0) AS unread_count
               {pin_field}
        FROM users u
        LEFT JOIN conversations conv ON (
            (conv.user1_id = ? AND conv.user2_id = u.id) OR
            (conv.user2_id = ? AND conv.user1_id = u.id)
        )
        WHERE u.id != ?
    """
    params = [user_id]
    if admin_pins:
        params.append(user_id)
    params.extend([user_id, user_id, user_id])
    if admins_only:
        query += " AND u.is_admin = 1"
    if search:
        query += " AND u.username LIKE ?"
        params.append(f"%{search}%")

    order_parts = []
    if admin_pins:
        params.append(user_id)
        order_parts.append("""
            CASE WHEN EXISTS (
                SELECT 1 FROM pinned_conversations pc
                WHERE pc.user_id = ? AND pc.conversation_id = conv.id
            ) THEN 0 ELSE 1 END
        """)
    order_parts.append("""
        (
            SELECT MAX(messages.created_at)
            FROM messages
            WHERE messages.conversation_id = conv.id
        ) DESC NULLS LAST
    """)
    order_parts.append("u.username ASC")
    query += " ORDER BY " + ", ".join(order_parts)
    return db.execute(query, params).fetchall()


def get_popular_tags(limit=20):
    db = get_db()
    cutoff = publication_cutoff()
    return db.execute(f"""
        SELECT tags.id, tags.name, tags.slug,
               COUNT(post_tags.post_id) AS post_count
        FROM tags
        JOIN post_tags ON post_tags.tag_id = tags.id
        JOIN posts ON posts.id = post_tags.post_id
            AND posts.is_private = 0
            AND {post_is_public_sql("posts")}
        GROUP BY tags.id
        HAVING COUNT(post_tags.post_id) > 0
        ORDER BY post_count DESC, tags.name
        LIMIT ?
    """, (cutoff, limit)).fetchall()


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


def fetch_posts(category_slug=None, tag_slug=None, search=None):
    db = get_db()
    cutoff = publication_cutoff()
    access_sql, access_params = build_post_access_filter(getattr(g, "user", None))
    engagement_sql, engagement_params = post_engagement_sql()
    query = f"""
        SELECT posts.id, posts.title, posts.content, posts.image, posts.created_at,
               posts.published_at, posts.is_private, posts.is_pinned,
               users.username, users.id AS author_id, users.last_seen AS author_last_seen,
               users.xp AS author_xp,
               categories.name AS category_name, categories.slug AS category_slug,
               {engagement_sql}
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
    """
    params = [*engagement_params, cutoff, *access_params]
    conditions = [post_is_public_sql("posts"), access_sql]

    if category_slug:
        conditions.append("categories.slug = ?")
        params.append(category_slug)
    if tag_slug:
        query += " JOIN post_tags ON post_tags.post_id = posts.id JOIN tags ON tags.id = post_tags.tag_id"
        conditions.append("tags.slug = ?")
        params.append(tag_slug)
    if search:
        conditions.append("(posts.title LIKE ? OR posts.content LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY posts.is_pinned DESC, COALESCE(posts.published_at, posts.created_at) DESC"
    return db.execute(query, params).fetchall()


def fetch_related_posts(post, limit=3):
    if not post.get("category_id"):
        return []
    db = get_db()
    cutoff = publication_cutoff()
    access_sql, access_params = build_post_access_filter(getattr(g, "user", None))
    engagement_sql, engagement_params = post_engagement_sql()
    query = f"""
        SELECT posts.id, posts.title, posts.content, posts.image, posts.created_at,
               posts.published_at, posts.is_private, posts.is_pinned,
               users.username, users.id AS author_id, users.last_seen AS author_last_seen,
               users.xp AS author_xp,
               categories.name AS category_name, categories.slug AS category_slug,
               {engagement_sql}
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
        WHERE posts.id != ?
          AND posts.category_id = ?
          AND {post_is_public_sql("posts")}
          AND {access_sql}
        ORDER BY posts.is_pinned DESC, COALESCE(posts.published_at, posts.created_at) DESC
        LIMIT ?
    """
    params = [
        post["id"],
        post["category_id"],
        *engagement_params,
        cutoff,
        *access_params,
        limit,
    ]
    return attach_tags_to_posts(db.execute(query, params).fetchall())


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
    g.pending_proposals = 0
    if "user_id" in session:
        try:
            db = get_db()
            g.user = db.execute(
                "SELECT id, username, email, is_admin, bio, avatar, last_seen, xp "
                "FROM users WHERE id = ?",
                (session["user_id"],),
            ).fetchone()
            if g.user:
                touch_last_seen(g.user["id"])
                g.unread_messages = get_unread_count(g.user["id"])
                if g.user["is_admin"]:
                    g.pending_proposals = get_pending_proposals_count()
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
        "site_byline": SITE_BYLINE,
        "media_url": media_url,
        "post_cover": post_cover_filename,
        "absolute_media_url": absolute_media_url,
        "db_persistent": database.IS_PERSISTENT or not IS_RENDER,
        "is_post_scheduled": is_post_scheduled,
        "is_post_published": is_post_published,
        "is_post_private": is_post_private,
        "is_post_pinned": is_post_pinned,
        "can_manage_post_access": can_manage_post_access,
        "format_datetime_local": format_datetime_local,
        "get_user_level_info": get_user_level_info,
        "USER_LEVELS": USER_LEVELS,
        "pending_proposals": getattr(g, "pending_proposals", 0),
        "proposal_status_label": proposal_status_label,
    }


def render_posts_page(category_slug=None, tag_slug=None, search=None):
    categories = get_categories()
    tags = get_popular_tags()
    posts_raw = fetch_posts(
        category_slug=category_slug, tag_slug=tag_slug, search=search
    )
    posts = attach_tags_to_posts(posts_raw)

    page_title = "Последние посты"
    page_subtitle = None
    active_category = None
    active_tag = None
    search_query = search

    if search:
        page_title = f"Поиск: {search}"
        page_subtitle = f"Найдено постов: {len(posts)}"
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

    show_welcome = not search and not category_slug and not tag_slug
    featured_post = posts[0] if show_welcome and posts else None

    return render_template(
        "index.html",
        posts=posts,
        categories=categories,
        tags=tags,
        page_title=page_title,
        page_subtitle=page_subtitle,
        active_category=active_category,
        active_tag=active_tag,
        search_query=search_query,
        show_welcome=show_welcome,
        featured_post=featured_post,
    )


@app.route("/")
def index():
    return render_posts_page()


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("index"))
    return render_posts_page(search=query)


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
                award_xp(new_user_id, XP_REWARD["register"])
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
        "SELECT id, username, bio, avatar, created_at, is_admin, last_seen, xp "
        "FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if not profile_user:
        flash("Пользователь не найден.", "error")
        return redirect(url_for("index"))

    engagement_sql, engagement_params = post_engagement_sql()
    posts_query = f"""
        SELECT posts.id, posts.title, posts.content, posts.image, posts.created_at,
               posts.published_at, posts.is_private,
               users.username, users.id AS author_id, users.xp AS author_xp,
               users.last_seen AS author_last_seen,
               categories.name AS category_name, categories.slug AS category_slug,
               {engagement_sql}
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
        WHERE posts.user_id = ?
    """
    posts_params = [*engagement_params, profile_user["id"]]
    viewer_is_owner = g.user and g.user["id"] == profile_user["id"]
    if viewer_is_owner or is_admin():
        pass
    elif g.user:
        posts_query += f"""
            AND {post_is_public_sql('posts')}
            AND (
                posts.is_private = 0
                OR posts.id IN (SELECT post_id FROM post_access WHERE user_id = ?)
            )
        """
        posts_params.extend([publication_cutoff(), g.user["id"]])
    else:
        posts_query += f" AND posts.is_private = 0 AND {post_is_public_sql('posts')}"
        posts_params.append(publication_cutoff())
    posts_query += " ORDER BY COALESCE(posts.published_at, posts.created_at) DESC"
    posts_raw = db.execute(posts_query, posts_params).fetchall()
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
            "UPDATE users SET avatar = ? WHERE id = ?",
            (avatar, session["user_id"]),
        )
        db.commit()
        flash("Профиль обновлён!", "success")
        return redirect(url_for("user_profile", username=g.user["username"]))

    return render_template("edit_profile.html")


def get_pending_proposals_count():
    db = get_db()
    return db.execute(
        "SELECT COUNT(*) FROM post_proposals WHERE status = ?",
        (PROPOSAL_STATUS_PENDING,),
    ).fetchone()[0]


def proposal_status_label(status):
    return {
        PROPOSAL_STATUS_PENDING: "На рассмотрении",
        PROPOSAL_STATUS_APPROVED: "Одобрена",
        PROPOSAL_STATUS_REJECTED: "Отклонена",
    }.get(status, status)


def validate_post_fields(title, content, category_id):
    errors = []
    if len(title) < 3:
        errors.append("Заголовок — минимум 3 символа.")
    if len(content) < 10:
        errors.append("Текст поста — минимум 10 символов.")
    if not category_id:
        errors.append("Выберите категорию.")
    elif not errors:
        db = get_db()
        cat = db.execute(
            "SELECT id FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not cat:
            errors.append("Категория не найдена.")
    return errors


def proposal_form_context():
    db = get_db()
    categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    return {"categories": categories, "inline_images": []}


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
        "schedule_enabled": bool(post and is_post_scheduled(post)),
        "publish_at_local": (
            format_datetime_local(post.get("published_at"))
            if post and post.get("published_at")
            else ""
        ),
        "is_private_checked": bool(post and post.get("is_private")),
        "inline_images": extract_inline_images(post["content"]) if post else [],
    }


@app.route("/post/propose", methods=["GET", "POST"])
@login_required
def propose_post():
    if is_admin():
        return redirect(url_for("create_post"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        category_id = request.form.get("category_id", type=int)
        tags_raw = request.form.get("tags", "")
        image = save_upload(request.files.get("image"))
        content = embed_content_images(
            content, request.files.getlist("content_images")
        )

        if request.files.get("image") and request.files["image"].filename and not image:
            flash("Допустимые форматы обложки: PNG, JPG, GIF, WEBP.", "error")
            return render_template("propose_post.html", **proposal_form_context())

        errors = validate_post_fields(title, content, category_id)
        if not errors:
            now = datetime.now().isoformat()
            db = get_db()
            db.execute(
                """INSERT INTO post_proposals
                   (user_id, title, content, image, category_id, tags, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session["user_id"],
                    title,
                    content,
                    image,
                    category_id,
                    tags_raw.strip(),
                    PROPOSAL_STATUS_PENDING,
                    now,
                ),
            )
            award_xp(session["user_id"], XP_REWARD["proposal"])
            db.commit()
            flash(
                "Заявка отправлена! Администратор рассмотрит её и опубликует пост, "
                "если всё в порядке.",
                "success",
            )
            return redirect(url_for("my_proposals"))

        for error in errors:
            flash(error, "error")

    return render_template("propose_post.html", **proposal_form_context())


@app.route("/proposals")
@login_required
def my_proposals():
    db = get_db()
    proposals = db.execute(
        """
        SELECT post_proposals.id, post_proposals.title, post_proposals.status,
               post_proposals.created_at, post_proposals.admin_note, post_proposals.post_id,
               categories.name AS category_name
        FROM post_proposals
        LEFT JOIN categories ON categories.id = post_proposals.category_id
        WHERE post_proposals.user_id = ?
        ORDER BY post_proposals.created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template("my_proposals.html", proposals=proposals)


@app.route("/admin/proposals")
@admin_required
def admin_proposals():
    db = get_db()
    status = request.args.get("status", PROPOSAL_STATUS_PENDING)
    if status not in (
        PROPOSAL_STATUS_PENDING,
        PROPOSAL_STATUS_APPROVED,
        PROPOSAL_STATUS_REJECTED,
        "all",
    ):
        status = PROPOSAL_STATUS_PENDING

    query = """
        SELECT post_proposals.id, post_proposals.title, post_proposals.content,
               post_proposals.image, post_proposals.tags, post_proposals.status,
               post_proposals.admin_note, post_proposals.created_at, post_proposals.post_id,
               post_proposals.reviewed_at,
               users.username, users.id AS author_id,
               categories.name AS category_name,
               reviewers.username AS reviewer_name
        FROM post_proposals
        JOIN users ON users.id = post_proposals.user_id
        LEFT JOIN categories ON categories.id = post_proposals.category_id
        LEFT JOIN users reviewers ON reviewers.id = post_proposals.reviewed_by
    """
    params = []
    if status != "all":
        query += " WHERE post_proposals.status = ?"
        params.append(status)
    query += " ORDER BY post_proposals.created_at DESC"

    proposals = db.execute(query, params).fetchall()
    counts = {
        "pending": db.execute(
            "SELECT COUNT(*) FROM post_proposals WHERE status = ?",
            (PROPOSAL_STATUS_PENDING,),
        ).fetchone()[0],
        "approved": db.execute(
            "SELECT COUNT(*) FROM post_proposals WHERE status = ?",
            (PROPOSAL_STATUS_APPROVED,),
        ).fetchone()[0],
        "rejected": db.execute(
            "SELECT COUNT(*) FROM post_proposals WHERE status = ?",
            (PROPOSAL_STATUS_REJECTED,),
        ).fetchone()[0],
    }
    return render_template(
        "admin_proposals.html",
        proposals=proposals,
        active_status=status,
        counts=counts,
    )


@app.route("/admin/proposals/<int:proposal_id>")
@admin_required
def review_proposal(proposal_id):
    db = get_db()
    proposal = db.execute(
        """
        SELECT post_proposals.*, users.username, categories.name AS category_name
        FROM post_proposals
        JOIN users ON users.id = post_proposals.user_id
        LEFT JOIN categories ON categories.id = post_proposals.category_id
        WHERE post_proposals.id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if not proposal:
        flash("Заявка не найдена.", "error")
        return redirect(url_for("admin_proposals"))
    return render_template("review_proposal.html", proposal=proposal)


@app.route("/admin/proposals/<int:proposal_id>/approve", methods=["POST"])
@admin_required
def approve_proposal(proposal_id):
    db = get_db()
    proposal = db.execute(
        "SELECT * FROM post_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if not proposal:
        flash("Заявка не найдена.", "error")
        return redirect(url_for("admin_proposals"))
    if proposal["status"] != PROPOSAL_STATUS_PENDING:
        flash("Эта заявка уже рассмотрена.", "warning")
        return redirect(url_for("review_proposal", proposal_id=proposal_id))

    now = datetime.now().isoformat()
    cur = db.execute(
        """INSERT INTO posts
           (user_id, title, content, image, category_id, created_at, updated_at,
            published_at, is_private)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            proposal["user_id"],
            proposal["title"],
            proposal["content"],
            proposal["image"],
            proposal["category_id"],
            now,
            now,
            now,
        ),
    )
    post_id = cur.lastrowid
    if not post_id:
        flash("Не удалось создать пост.", "error")
        return redirect(url_for("review_proposal", proposal_id=proposal_id))

    set_post_tags(post_id, parse_tags_input(proposal["tags"] or ""))
    db.execute(
        """UPDATE post_proposals
           SET status = ?, reviewed_by = ?, reviewed_at = ?, post_id = ?
           WHERE id = ?""",
        (
            PROPOSAL_STATUS_APPROVED,
            session["user_id"],
            now,
            post_id,
            proposal_id,
        ),
    )
    award_xp(proposal["user_id"], XP_REWARD["post"])
    db.commit()
    flash("Заявка одобрена, пост опубликован!", "success")
    return redirect(url_for("view_post", post_id=post_id))


@app.route("/admin/proposals/<int:proposal_id>/reject", methods=["POST"])
@admin_required
def reject_proposal(proposal_id):
    db = get_db()
    proposal = db.execute(
        "SELECT * FROM post_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if not proposal:
        flash("Заявка не найдена.", "error")
        return redirect(url_for("admin_proposals"))
    if proposal["status"] != PROPOSAL_STATUS_PENDING:
        flash("Эта заявка уже рассмотрена.", "warning")
        return redirect(url_for("review_proposal", proposal_id=proposal_id))

    admin_note = request.form.get("admin_note", "").strip()[:500]
    now = datetime.now().isoformat()
    if proposal["image"]:
        delete_file(proposal["image"])
    db.execute(
        """UPDATE post_proposals
           SET status = ?, admin_note = ?, reviewed_by = ?, reviewed_at = ?, image = NULL
           WHERE id = ?""",
        (
            PROPOSAL_STATUS_REJECTED,
            admin_note,
            session["user_id"],
            now,
            proposal_id,
        ),
    )
    db.commit()
    flash("Заявка отклонена.", "info")
    return redirect(url_for("admin_proposals"))


@app.route("/post/new", methods=["GET", "POST"])
@admin_required
def create_post():
    try:
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            category_id = request.form.get("category_id", type=int)
            tags_raw = request.form.get("tags", "")
            image = save_upload(request.files.get("image"))
            content = embed_content_images(
                content, request.files.getlist("content_images")
            )

            if request.files.get("image") and request.files["image"].filename and not image:
                flash("Допустимые форматы обложки: PNG, JPG, GIF, WEBP.", "error")
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

            published_at, is_scheduled, schedule_error = parse_publish_schedule(request.form)
            if schedule_error:
                errors.append(schedule_error)

            if not errors:
                now = datetime.now().isoformat()
                is_private = 1 if request.form.get("is_private") == "on" else 0
                db = get_db()
                cur = db.execute(
                    """INSERT INTO posts
                       (user_id, title, content, image, category_id, created_at, updated_at,
                        published_at, is_private)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session["user_id"],
                        title,
                        content,
                        image,
                        category_id,
                        now,
                        now,
                        published_at,
                        is_private,
                    ),
                )
                post_id = cur.lastrowid
                if not post_id:
                    raise RuntimeError("post insert did not return id")
                set_post_tags(post_id, parse_tags_input(tags_raw))
                if not is_scheduled:
                    award_xp(session["user_id"], XP_REWARD["post"])
                db.commit()
                if is_scheduled:
                    flash(
                        f"Пост запланирован на {published_at[:16].replace('T', ' ')}.",
                        "success",
                    )
                elif is_private:
                    flash(
                        "Приватный пост создан. Выдайте доступ на странице поста.",
                        "success",
                    )
                else:
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
    engagement_sql, engagement_params = post_engagement_sql()
    post = db.execute(
        f"""
        SELECT posts.id, posts.title, posts.content, posts.image,
               posts.created_at, posts.updated_at, posts.category_id, posts.published_at,
               posts.is_private, posts.is_pinned, posts.user_id,
               users.username, users.id AS author_id, users.xp AS author_xp,
               categories.name AS category_name, categories.slug AS category_slug,
               {engagement_sql}
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
        WHERE posts.id = ?
        """,
        (*engagement_params, post_id),
    ).fetchone()

    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))

    can_preview_unpublished = g.user and (
        g.user["id"] == post["author_id"] or is_admin()
    )
    if not is_post_published(post) and not can_preview_unpublished:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))
    if is_post_published(post) and not can_view_post(post):
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
    is_scheduled_preview = not is_post_published(post)
    related_posts = fetch_related_posts(post) if is_post_published(post) else []

    return render_template(
        "post.html",
        post=post,
        comments=comments,
        post_tags=post_tags,
        is_scheduled_preview=is_scheduled_preview,
        related_posts=related_posts,
    )


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
        old_inline_images = set(extract_inline_images(post["content"]))
        content = embed_content_images(
            content, request.files.getlist("content_images")
        )

        if request.files.get("image") and request.files["image"].filename and not new_image:
            flash("Допустимые форматы обложки: PNG, JPG, GIF, WEBP.", "error")
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

        published_at, is_scheduled, schedule_error = resolve_edit_publish_at(
            request.form, post
        )
        if schedule_error:
            errors.append(schedule_error)

        is_private = 1 if request.form.get("is_private") == "on" else 0

        if not errors:
            db.execute(
                """UPDATE posts SET title = ?, content = ?, image = ?, category_id = ?,
                   updated_at = ?, published_at = ?, is_private = ?
                   WHERE id = ?""",
                (
                    title,
                    content,
                    image,
                    category_id,
                    datetime.now().isoformat(),
                    published_at,
                    is_private,
                    post_id,
                ),
            )
            set_post_tags(post_id, parse_tags_input(tags_raw))
            if not is_private:
                db.execute("DELETE FROM post_access WHERE post_id = ?", (post_id,))
            new_inline_images = set(extract_inline_images(content))
            for filename in old_inline_images - new_inline_images:
                delete_file(filename)
            db.commit()
            if is_scheduled:
                flash(
                    f"Пост запланирован на {published_at[:16].replace('T', ' ')}.",
                    "success",
                )
            else:
                flash("Пост обновлён!", "success")
            return redirect(url_for("view_post", post_id=post_id))

        for error in errors:
            flash(error, "error")

    return render_template("edit_post.html", post=post, **post_form_context(post))


@app.route("/admin/post/<int:post_id>/pin", methods=["POST"])
@admin_required
def toggle_post_pin(post_id):
    db = get_db()
    post = db.execute(
        "SELECT id, is_pinned FROM posts WHERE id = ?", (post_id,)
    ).fetchone()

    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("admin_panel"))

    pinned = 0 if is_post_pinned(post) else 1
    db.execute("UPDATE posts SET is_pinned = ? WHERE id = ?", (pinned, post_id))
    db.commit()
    flash("Пост закреплён." if pinned else "Закрепление снято.", "info")
    return redirect(url_for("admin_panel"))


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
    delete_inline_images_from_content(post["content"])
    db.execute("DELETE FROM post_access WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM post_tags WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM post_likes WHERE post_id = ?", (post_id,))
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
    post = db.execute(
        "SELECT id, user_id, created_at, published_at, is_private FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))
    if not is_post_published(post):
        flash("Комментарии доступны только после публикации поста.", "warning")
        return redirect(url_for("view_post", post_id=post_id))
    if not can_view_post(post):
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
    award_xp(session["user_id"], XP_REWARD["comment"])
    db.commit()
    flash("Комментарий добавлен!", "success")
    return redirect(url_for("view_post", post_id=post_id))


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_post_like(post_id):
    db = get_db()
    post = db.execute(
        "SELECT id, user_id, published_at, created_at, is_private FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))
    if not is_post_published(post):
        flash("Лайкать можно только опубликованные посты.", "warning")
        return redirect(url_for("view_post", post_id=post_id))
    if not can_view_post(post):
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))

    user_id = session["user_id"]
    existing = db.execute(
        "SELECT 1 FROM post_likes WHERE post_id = ? AND user_id = ?",
        (post_id, user_id),
    ).fetchone()

    if existing:
        db.execute(
            "DELETE FROM post_likes WHERE post_id = ? AND user_id = ?",
            (post_id, user_id),
        )
        liked = False
    else:
        db.execute(
            "INSERT INTO post_likes (post_id, user_id, created_at) VALUES (?, ?, ?)",
            (post_id, user_id, datetime.now().isoformat()),
        )
        award_xp(user_id, XP_REWARD["like"])
        if post["user_id"] != user_id:
            award_xp(post["user_id"], XP_REWARD["liked"])
        liked = True

    db.commit()
    like_count = db.execute(
        "SELECT COUNT(*) FROM post_likes WHERE post_id = ?",
        (post_id,),
    ).fetchone()[0]

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"liked": liked, "like_count": like_count})

    return redirect(request.referrer or url_for("view_post", post_id=post_id))


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


@app.route("/messages/conversation/<int:conv_id>/pin", methods=["POST"])
@admin_required
def toggle_pin_conversation(conv_id):
    if not user_in_conversation(conv_id, session["user_id"]):
        abort(403)

    db = get_db()
    existing = db.execute(
        "SELECT 1 FROM pinned_conversations WHERE user_id = ? AND conversation_id = ?",
        (session["user_id"], conv_id),
    ).fetchone()

    if existing:
        db.execute(
            "DELETE FROM pinned_conversations WHERE user_id = ? AND conversation_id = ?",
            (session["user_id"], conv_id),
        )
        db.commit()
        flash("Чат откреплён.", "info")
    else:
        db.execute(
            """INSERT OR IGNORE INTO pinned_conversations
               (user_id, conversation_id, pinned_at)
               VALUES (?, ?, ?)""",
            (session["user_id"], conv_id, datetime.now().isoformat()),
        )
        db.commit()
        flash("Чат закреплён.", "success")

    next_url = request.form.get("next") or request.referrer
    if not next_url:
        next_url = url_for("messages_inbox")
    return redirect(next_url)


@app.route("/messages")
@login_required
def messages_inbox():
    try:
        admins_only = not g.user["is_admin"]
        search = request.args.get("q", "").strip() if g.user["is_admin"] else ""
        contacts = get_message_contacts(
            g.user["id"],
            search,
            admins_only=admins_only,
            admin_pins=g.user["is_admin"],
        )
        return render_template(
            "messages.html",
            contacts=contacts,
            search=search,
            admins_only=admins_only,
        )
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
    if not g.user["is_admin"]:
        return jsonify([])
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    db = get_db()
    admins_only = not g.user["is_admin"]
    sql = """SELECT id, username, avatar FROM users
             WHERE id != ? AND username LIKE ?"""
    params = [session["user_id"], f"%{query}%"]
    if admins_only:
        sql += " AND is_admin = 1"
    sql += " ORDER BY username LIMIT 20"
    rows = db.execute(sql, params).fetchall()
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
        "SELECT id, username, avatar, last_seen, is_admin, xp FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if not partner:
        flash("Пользователь не найден.", "error")
        return redirect(url_for("messages_inbox"))

    if partner["id"] == g.user["id"]:
        flash("Нельзя написать самому себе.", "warning")
        return redirect(url_for("messages_inbox"))

    if not g.user["is_admin"] and not partner["is_admin"]:
        flash("Обычные пользователи могут писать только администраторам.", "error")
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
            award_xp(g.user["id"], XP_REWARD["message"])
            db.commit()
            return redirect(url_for("chat_with_user", username=username))

    db.execute("""
        UPDATE messages SET is_read = 1
        WHERE conversation_id = ? AND sender_id != ? AND is_read = 0
    """, (conv_id, g.user["id"]))
    db.commit()

    messages = db.execute("""
        SELECT messages.id, messages.content, messages.created_at, messages.sender_id,
               users.username AS sender_name, users.is_admin AS sender_is_admin
        FROM messages
        JOIN users ON users.id = messages.sender_id
        WHERE messages.conversation_id = ?
        ORDER BY messages.created_at ASC
    """, (conv_id,)).fetchall()

    admins_only = not g.user["is_admin"]
    contacts = get_message_contacts(
        g.user["id"],
        admins_only=admins_only,
        admin_pins=g.user["is_admin"],
    )

    return render_template(
        "chat.html",
        partner=partner,
        messages=messages,
        conv_id=conv_id,
        contacts=contacts,
        admins_only=admins_only,
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
               users.username AS sender_name, users.is_admin AS sender_is_admin
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
                "sender_is_admin": bool(r["sender_is_admin"]),
                "is_mine": r["sender_id"] == session["user_id"],
            }
            for r in rows
        ],
        "partner_online": partner_online,
    })


@app.route("/post/<int:post_id>/access", methods=["GET", "POST"])
@login_required
def post_access(post_id):
    db = get_db()
    post = db.execute("""
        SELECT posts.id, posts.title, posts.is_private, posts.user_id,
               users.username AS author_name
        FROM posts
        JOIN users ON users.id = posts.user_id
        WHERE posts.id = ?
    """, (post_id,)).fetchone()

    if not post:
        flash("Пост не найден.", "error")
        return redirect(url_for("index"))

    if not can_manage_post_access(post):
        flash("У вас нет прав управлять доступом к этому посту.", "error")
        return redirect(url_for("view_post", post_id=post_id))

    if not post["is_private"]:
        flash("Этот пост публичный — управление доступом не требуется.", "info")
        return redirect(post_access_back_url(post))

    if request.method == "POST":
        action = request.form.get("action")
        user_id = request.form.get("user_id", type=int)

        if action == "grant" and user_id:
            if user_id == post["user_id"]:
                flash("Автору поста доступ не нужно выдавать.", "warning")
            else:
                target = db.execute(
                    "SELECT id, username FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if not target:
                    flash("Пользователь не найден.", "error")
                else:
                    db.execute(
                        """INSERT OR IGNORE INTO post_access
                           (post_id, user_id, granted_at, granted_by)
                           VALUES (?, ?, ?, ?)""",
                        (
                            post_id,
                            user_id,
                            datetime.now().isoformat(),
                            session["user_id"],
                        ),
                    )
                    db.commit()
                    flash(f"Доступ выдан пользователю {target['username']}.", "success")
        elif action == "revoke" and user_id:
            target = db.execute(
                "SELECT username FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            db.execute(
                "DELETE FROM post_access WHERE post_id = ? AND user_id = ?",
                (post_id, user_id),
            )
            db.commit()
            if target:
                flash(f"Доступ отозван у {target['username']}.", "info")

        return redirect(url_for("post_access", post_id=post_id))

    granted_users = get_post_access_users(post_id)
    available_users = db.execute("""
        SELECT id, username FROM users
        WHERE id != ?
          AND id NOT IN (SELECT user_id FROM post_access WHERE post_id = ?)
        ORDER BY username
    """, (post["user_id"], post_id)).fetchall()

    return render_template(
        "post_access.html",
        post=post,
        granted_users=granted_users,
        available_users=available_users,
        back_url=post_access_back_url(post),
    )


@app.route("/admin/post/<int:post_id>/access", methods=["GET", "POST"])
@login_required
def admin_post_access_redirect(post_id):
    return post_access(post_id)


@app.route("/admin")
@admin_required
def admin_panel():
    db = get_db()
    posts = db.execute("""
        SELECT posts.id, posts.title, posts.created_at, posts.published_at, posts.is_private,
               posts.is_pinned,
               users.username, users.id AS author_id,
               categories.name AS category_name,
               (SELECT COUNT(*) FROM post_access WHERE post_access.post_id = posts.id) AS access_count
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
        ORDER BY posts.is_pinned DESC, COALESCE(posts.published_at, posts.created_at) DESC
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
        "pending_proposals": get_pending_proposals_count(),
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
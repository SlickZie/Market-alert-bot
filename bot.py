
import logging
import os
import sqlite3
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DATABASE_PATH", "bot_data.db")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WAITING_LINK = "waiting_link"
WAITING_TEXT = "waiting_text"
WAITING_IMPRESSIONS = "waiting_impressions"


def database():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with database() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                post_text TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(user_id, url)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                impressions INTEGER NOT NULL,
                recorded_at TEXT NOT NULL
            )
        """)


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Post toevoegen", callback_data="add")],
        [InlineKeyboardButton("📚 Mijn posts", callback_data="posts")],
        [InlineKeyboardButton("📊 Statistieken", callback_data="stats")],
        [InlineKeyboardButton("ℹ️ Uitleg", callback_data="info")],
    ])


def valid_link(url):
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        return (
            parsed.scheme in ("https", "http")
            and (host == "linkedin.com" or host.endswith(".linkedin.com"))
        )
    except Exception:
        return False


def get_post(post_id, user_id):
    with database() as conn:
        return conn.execute(
            "SELECT * FROM posts WHERE id=? AND user_id=?",
            (post_id, user_id),
        ).fetchone()


def get_posts(user_id):
    with database() as conn:
        return conn.execute(
            "SELECT * FROM posts WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        ).fetchall()


def topic(text):
    text = (text or "").lower()

    categories = [
        (["lassen", "laswerk", "kwaliteit", "qc", "qa"], "kwaliteit en vakmanschap"),
        (["project", "leiding", "installatie", "bouw"], "het project"),
        (["vacature", "collega", "werken"], "de vacature"),
        (["opleiding", "diploma", "leren", "iwt"], "ontwikkeling en vakmanschap"),
        (["team", "samenwerking"], "de samenwerking"),
    ]

    for words, result in categories:
        if any(word in text for word in words):
            return result

    return "dit onderwerp"


def suggestions(text):
    subject = topic(text)

    return [
        f"Mooi om te zien dat {subject} aandacht krijgt. Wat was hierin de grootste uitdaging?",
        f"Interessant om te lezen. Hoe hebben jullie dit in de praktijk aangepakt?",
        f"Mooi resultaat! Welke ervaring nemen jullie mee naar een volgend project?",
    ]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "🚀 LINKEDIN REACH BOOSTER\n\n"
        "Selecteer bestaande LinkedIn-posts en maak een plan "
        "om de organische zichtbaarheid te vergroten.\n\n"
        "Geen automatische likes of reacties.",
        reply_markup=menu(),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    if state == WAITING_LINK:

        if not valid_link(text):
            await update.message.reply_text(
                "Dit lijkt geen geldige LinkedIn-link."
            )
            return

        context.user_data["pending_url"] = text
        context.user_data["state"] = WAITING_TEXT

        await update.message.reply_text(
            "Link ontvangen!\n\n"
            "Plak nu de tekst van de LinkedIn-post.\n"
            "Of stuur OVERSLAAN."
        )
        return

    if state == WAITING_TEXT:

        post_text = "" if text.lower() == "overslaan" else text[:12000]

        with database() as conn:
            conn.execute(
                """
                INSERT INTO posts
                (user_id, url, post_text, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, url)
                DO UPDATE SET post_text=excluded.post_text
                """,
                (
                    user_id,
                    context.user_data["pending_url"],
                    post_text,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ LinkedIn-post opgeslagen!",
            reply_markup=menu(),
        )
        return

    if state == WAITING_IMPRESSIONS:

        if not text.isdigit():
            await update.message.reply_text(
                "Stuur alleen een getal, bijvoorbeeld 1250."
            )
            return

        post_id = context.user_data["post_id"]

        with database() as conn:
            conn.execute(
                """
                INSERT INTO measurements
                (post_id, user_id, impressions, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    post_id,
                    user_id,
                    int(text),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        context.user_data.clear()

        await update.message.reply_text(
            "📊 Impressies opgeslagen!",
            reply_markup=menu(),
        )
        return

    await update.message.reply_text(
        "Gebruik /start om het menu te openen."
    )


async def show_posts(query, user_id):
    posts = get_posts(user_id)

    if not posts:
        await query.edit_message_text(
            "Je hebt nog geen posts opgeslagen.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Toevoegen", callback_data="add")]
            ]),
        )
        return

    buttons = []

    for post in posts[:20]:
        label = post["post_text"] or post["url"]
        label = label.replace("\n", " ")[:35]

        buttons.append([
            InlineKeyboardButton(
                "📌 " + label,
                callback_data=f"post:{post['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("⬅️ Hoofdmenu", callback_data="home")
    ])

    await query.edit_message_text(
        "Kies een bestaande LinkedIn-post:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_post(query, post_id, user_id):
    post = get_post(post_id, user_id)

    if not post:
        await query.edit_message_text("Post niet gevonden.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Boostplan", callback_data=f"boost:{post_id}")],
        [InlineKeyboardButton("💬 Reactiesuggesties", callback_data=f"comments:{post_id}")],
        [InlineKeyboardButton("📊 Impressies invoeren", callback_data=f"measure:{post_id}")],
        [InlineKeyboardButton("🔗 Open LinkedIn", url=post["url"])],
        [InlineKeyboardButton("⬅️ Terug", callback_data="posts")],
    ])

    await query.edit_message_text(
        f"📌 POST #{post_id}\n\n"
        f"{escape((post['post_text'] or 'Geen posttekst opgeslagen')[:500])}",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def show_boost(query, post_id, user_id):
    post = get_post(post_id, user_id)

    if not post:
        await query.edit_message_text("Post niet gevonden.")
        return

    await query.edit_message_text(
        "🚀 ORGANISCH BOOSTPLAN\n\n"
        "1. Deel een persoonlijke ervaring die aansluit bij de post.\n"
        "2. Stel een relevante vraag.\n"
        "3. Reageer inhoudelijk op antwoorden.\n"
        "4. Deel de post alleen met mensen voor wie hij relevant is.\n"
        "5. Meet later de ontwikkeling van de impressies.\n\n"
        "Extra bereik is niet gegarandeerd.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Reacties", callback_data=f"comments:{post_id}")],
            [InlineKeyboardButton("🔗 Open post", url=post["url"])],
            [InlineKeyboardButton("⬅️ Terug", callback_data=f"post:{post_id}")],
        ]),
    )


async def show_comments(query, post_id, user_id):
    post = get_post(post_id, user_id)

    if not post or not post["post_text"]:
        await query.edit_message_text(
            "Ik heb eerst de tekst van de post nodig."
        )
        return

    comments = suggestions(post["post_text"])

    keyboard = []

    for i in range(len(comments)):
        keyboard.append([
            InlineKeyboardButton(
                f"Reactie {i + 1}",
                callback_data=f"comment:{post_id}:{i}",
            )
        ])

    await query.edit_message_text(
        "💬 Kies een reactiesuggestie:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_comment(query, post_id, index, user_id):
    post = get_post(post_id, user_id)

    if not post:
        await query.edit_message_text("Post niet gevonden.")
        return

    comments = suggestions(post["post_text"])

    if index >= len(comments):
        return

    await query.edit_message_text(
        "💬 Reactiesuggestie:\n\n" + comments[index],
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open LinkedIn", url=post["url"])],
            [InlineKeyboardButton("⬅️ Terug", callback_data=f"comments:{post_id}")],
        ]),
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "home":
        context.user_data.clear()
        await query.edit_message_text(
            "🚀 LINKEDIN REACH BOOSTER",
            reply_markup=menu(),
        )

    elif data == "add":
        context.user_data["state"] = WAITING_LINK
        await query.edit_message_text(
            "Stuur de volledige LinkedIn-postlink."
        )

    elif data == "posts":
        context.user_data.clear()
        await show_posts(query, user_id)

    elif data == "stats":
        with database() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]

            measurements = conn.execute(
                "SELECT COUNT(*) FROM measurements WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]

        await query.edit_message_text(
            f"📊 STATISTIEKEN\n\n"
            f"Opgeslagen posts: {count}\n"
            f"Impressiemetingen: {measurements}\n\n"
            "Impressies voer je handmatig in via LinkedIn."
        )

    elif data == "info":
        await query.edit_message_text(
            "Voeg een postlink toe, plak de tekst en selecteer "
            "de post om een boostplan te bekijken."
        )

    elif data.startswith("post:"):
        await show_post(query, int(data.split(":")[1]), user_id)

    elif data.startswith("boost:"):
        await show_boost(query, int(data.split(":")[1]), user_id)

    elif data.startswith("comments:"):
        await show_comments(query, int(data.split(":")[1]), user_id)

    elif data.startswith("comment:"):
        _, post_id, index = data.split(":")
        await show_comment(query, int(post_id), int(index), user_id)

    elif data.startswith("measure:"):
        post_id = int(data.split(":")[1])

        context.user_data["state"] = WAITING_IMPRESSIONS
        context.user_data["post_id"] = post_id

        await query.edit_message_text(
            "Hoeveel impressies heeft deze post momenteel?"
        )


async def error_handler(update, context):
    logger.exception("Bot error", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN ontbreekt in Railway Variables.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    app.add_error_handler(error_handler)

    logger.info("LinkedIn Booster gestart.")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

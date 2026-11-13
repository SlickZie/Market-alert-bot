
import os
import re
import sqlite3
import html
from urllib.parse import urlparse
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==========================================
# CONFIGURATIE
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

DATABASE_PATH = os.getenv("DATABASE_PATH", "booster.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN ontbreekt in de Railway environment variables.")


# ==========================================
# DATABASE
# ==========================================

def get_connection():
    return sqlite3.connect(DATABASE_PATH)


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                post_text TEXT DEFAULT '',
                impressions INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def save_post(user_id, url, post_text):
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO posts
            (user_id, url, post_text, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            user_id,
            url,
            post_text,
            datetime.now().strftime("%Y-%m-%d %H:%M")
        ))
        conn.commit()
        return cursor.lastrowid


def get_posts(user_id):
    with get_connection() as conn:
        return conn.execute("""
            SELECT id, url, post_text, impressions, created_at
            FROM posts
            WHERE user_id = ?
            ORDER BY id DESC
        """, (user_id,)).fetchall()


def get_post(post_id, user_id):
    with get_connection() as conn:
        return conn.execute("""
            SELECT id, url, post_text, impressions, created_at
            FROM posts
            WHERE id = ? AND user_id = ?
        """, (post_id, user_id)).fetchone()


def update_impressions(post_id, user_id, impressions):
    with get_connection() as conn:
        conn.execute("""
            UPDATE posts
            SET impressions = ?
            WHERE id = ? AND user_id = ?
        """, (impressions, post_id, user_id))
        conn.commit()


# ==========================================
# LINKEDIN LINKCONTROLE
# ==========================================

def is_valid_linkedin_url(url):
    try:
        url = url.strip().strip("<>")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urlparse(url)
        domain = parsed.netloc.lower().split(":")[0]

        valid_domain = (
            domain == "linkedin.com"
            or domain.endswith(".linkedin.com")
            or domain == "lnkd.in"
        )

        if not valid_domain:
            return False

        path = parsed.path.lower()

        if domain == "lnkd.in":
            return len(path.strip("/")) > 0

        return (
            "/posts/" in path
            or path.startswith("/feed/update/")
            or path.startswith("/pulse/")
            or "/activity-" in path
        )

    except Exception:
        return False


def clean_url(text):
    match = re.search(r"https?://\S+|(?:www\.)?linkedin\.com/\S+|lnkd\.in/\S+", text)

    if not match:
        return text.strip()

    return match.group(0).rstrip(".,;!?)")


# ==========================================
# MENU'S
# ==========================================

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Nieuwe LinkedIn-post", callback_data="new")],
        [InlineKeyboardButton("📋 Mijn posts", callback_data="posts")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ])


def post_menu(post_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Bereik verbeteren", callback_data=f"plan_{post_id}")],
        [InlineKeyboardButton("💬 Reactiesuggesties", callback_data=f"comments_{post_id}")],
        [InlineKeyboardButton("📊 Impressies invoeren", callback_data=f"impressions_{post_id}")],
        [InlineKeyboardButton("⬅️ Terug naar posts", callback_data="posts")]
    ])


# ==========================================
# START EN HELP
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "👋 Welkom bij LinkedIn Reach Booster!\n\n"
        "Met deze bot kun je openbare LinkedIn-posts toevoegen, "
        "ideeën krijgen om je bereik te verbeteren en zelf je impressies bijhouden.\n\n"
        "Kies een optie:",
        reply_markup=main_menu()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Hoe werkt de bot?\n\n"
        "1. Voeg een openbare LinkedIn-postlink toe.\n"
        "2. Voeg eventueel de tekst van je post toe.\n"
        "3. Bekijk ideeën om je bereik te verbeteren.\n"
        "4. Vraag reactiesuggesties op.\n"
        "5. Houd zelf je impressies bij.\n\n"
        "De bot plaatst geen likes, reacties of berichten namens jou."
    )


# ==========================================
# CALLBACKS
# ==========================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "new":
        context.user_data["step"] = "waiting_link"

        await query.message.reply_text(
            "🔗 Plak hier de volledige link van je openbare LinkedIn-post.\n\n"
            "Voorbeelden:\n"
            "linkedin.com/posts/gebruikersnaam_onderwerp-123456\n"
            "linkedin.com/feed/update/urn:li:activity:123456789"
        )

    elif data == "posts":
        posts = get_posts(user_id)

        if not posts:
            await query.message.reply_text(
                "Je hebt nog geen posts toegevoegd.",
                reply_markup=main_menu()
            )
            return

        keyboard = []

        for post in posts:
            post_id, url, post_text, impressions, created_at = post

            keyboard.append([
                InlineKeyboardButton(
                    f"Post #{post_id} – {impressions} impressies",
                    callback_data=f"select_{post_id}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton("➕ Nieuwe post", callback_data="new")
        ])

        await query.message.reply_text(
            "📋 Selecteer een post:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "help":
        await query.message.reply_text(
            "ℹ️ Voeg een openbare LinkedIn-post toe. "
            "Daarna kun je een bereikplan en reactiesuggesties bekijken "
            "en je impressies handmatig bijhouden."
        )

    elif data.startswith("select_"):
        post_id = int(data.split("_")[1])
        post = get_post(post_id, user_id)

        if not post:
            await query.message.reply_text("Post niet gevonden.")
            return

        _, url, post_text, impressions, created_at = post

        message = (
            f"📌 <b>Post #{post_id}</b>\n\n"
            f"📊 Impressies: {impressions}\n"
            f"📅 Toegevoegd: {html.escape(created_at)}\n\n"
            f"🔗 {html.escape(url)}"
        )

        if post_text:
            message += (
                "\n\n📝 <b>Posttekst:</b>\n"
                + html.escape(post_text[:1500])
            )

        await query.message.reply_text(
            message,
            parse_mode="HTML",
            reply_markup=post_menu(post_id),
            disable_web_page_preview=True
        )

    elif data.startswith("plan_"):
        post_id = int(data.split("_")[1])
        post = get_post(post_id, user_id)

        if not post:
            await query.message.reply_text("Post niet gevonden.")
            return

        await query.message.reply_text(
            "🚀 <b>Bereikplan</b>\n\n"
            "1. Reageer inhoudelijk op reacties onder je post.\n\n"
            "2. Deel een aanvullende gedachte in een eigen reactie.\n\n"
            "3. Reageer op relevante berichten van anderen in jouw vakgebied.\n\n"
            "4. Stel een gerichte vraag om een gesprek op gang te brengen.\n\n"
            "5. Deel de post alleen met mensen voor wie de inhoud relevant is.\n\n"
            "6. Bekijk na 24 en 48 uur hoe het bereik zich ontwikkelt.\n\n"
            "Tip: inhoudelijke interactie is waardevoller dan willekeurige reacties.",
            parse_mode="HTML"
        )

    elif data.startswith("comments_"):
        post_id = int(data.split("_")[1])
        post = get_post(post_id, user_id)

        if not post:
            await query.message.reply_text("Post niet gevonden.")
            return

        post_text = post[2].lower()

        suggestions = [
            "Interessant onderwerp! Wat zijn jullie ervaringen hiermee?",
            "Mooi om te zien hoe dit in de praktijk wordt toegepast.",
            "Een belangrijk punt. Hier wordt volgens mij nog te weinig aandacht aan besteed.",
            "Goed om deze ontwikkeling onder de aandacht te brengen.",
            "Ben benieuwd hoe anderen hier tegenaan kijken."
        ]

        if any(word in post_text for word in ["lassen", "welding", "leiding", "techniek"]):
            suggestions = [
                "Mooi praktijkvoorbeeld! Kwaliteit en vakmanschap blijven hierin belangrijk.",
                "Interessant om te zien. Welke uitdagingen komen jullie hierbij tegen?",
                "Goed om technische kennis op deze manier te delen.",
                "Herkenbaar onderwerp binnen de installatietechniek!",
                "Mooi werk! Welke normen of richtlijnen zijn hierbij van toepassing?"
            ]

        message = "💬 <b>Reactiesuggesties</b>\n\n"

        for i, suggestion in enumerate(suggestions, 1):
            message += f"{i}. {html.escape(suggestion)}\n\n"

        message += "Kies een reactie die bij je past en pas deze eventueel aan."

        await query.message.reply_text(
            message,
            parse_mode="HTML"
        )

    elif data.startswith("impressions_"):
        post_id = int(data.split("_")[1])

        if not get_post(post_id, user_id):
            await query.message.reply_text("Post niet gevonden.")
            return

        context.user_data["step"] = "waiting_impressions"
        context.user_data["selected_post"] = post_id

        await query.message.reply_text(
            "📊 Hoeveel impressies heeft deze post momenteel?\n\n"
            "Voer alleen het getal in, bijvoorbeeld: 1250"
        )


# ==========================================
# TEKSTVERWERKING
# ==========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    step = context.user_data.get("step")

    if step == "waiting_link":
        url = clean_url(text)

        if not is_valid_linkedin_url(url):
            await update.message.reply_text(
                "❌ Dit lijkt geen geldige LinkedIn-postlink.\n\n"
                "Plak de volledige link van een openbare post. "
                "Gebruik bijvoorbeeld een link die je via "
                "'Link kopiëren' op LinkedIn hebt verkregen."
            )
            return

        context.user_data["new_post_url"] = url
        context.user_data["step"] = "waiting_post_text"

        await update.message.reply_text(
            "✅ Link geaccepteerd!\n\n"
            "Plak nu de tekst van je LinkedIn-post.\n\n"
            "Heb je die niet bij de hand? Typ dan /skip."
        )

    elif step == "waiting_post_text":
        url = context.user_data.get("new_post_url")

        if not url:
            context.user_data.clear()
            await update.message.reply_text(
                "Er ging iets mis. Begin opnieuw via het menu.",
                reply_markup=main_menu()
            )
            return

        post_id = save_post(user_id, url, text)

        context.user_data.clear()

        await update.message.reply_text(
            f"✅ Je LinkedIn-post is opgeslagen!\n\n"
            f"Postnummer: #{post_id}",
            reply_markup=post_menu(post_id)
        )

    elif step == "waiting_impressions":
        if not text.isdigit():
            await update.message.reply_text(
                "❌ Voer alleen een heel getal in, bijvoorbeeld 1250."
            )
            return

        impressions = int(text)
        post_id = context.user_data.get("selected_post")

        if post_id is None:
            context.user_data.clear()
            await update.message.reply_text(
                "Geen post geselecteerd. Begin opnieuw via het menu.",
                reply_markup=main_menu()
            )
            return

        update_impressions(post_id, user_id, impressions)
        context.user_data.clear()

        await update.message.reply_text(
            f"✅ Impressies bijgewerkt naar {impressions:,}.".replace(",", "."),
            reply_markup=main_menu()
        )

    else:
        await update.message.reply_text(
            "Gebruik het menu om een actie te kiezen.",
            reply_markup=main_menu()
        )


# ==========================================
# POSTTEKST OVERSLAAN
# ==========================================

async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("step") != "waiting_post_text":
        await update.message.reply_text(
            "Er is geen posttekst om over te slaan."
        )
        return

    url = context.user_data.get("new_post_url")

    if not url:
        context.user_data.clear()
        await update.message.reply_text(
            "Begin opnieuw via het menu.",
            reply_markup=main_menu()
        )
        return

    post_id = save_post(update.effective_user.id, url, "")

    context.user_data.clear()

    await update.message.reply_text(
        f"✅ LinkedIn-post opgeslagen zonder posttekst.\n\n"
        f"Postnummer: #{post_id}",
        reply_markup=post_menu(post_id)
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "Actie geannuleerd.",
        reply_markup=main_menu()
    )


# ==========================================
# FOUTAFHANDELING
# ==========================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Bot error: {context.error}")


# ==========================================
# BOT STARTEN
# ==========================================

def main():
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("skip", skip))
    application.add_handler(CommandHandler("cancel", cancel))

    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    application.add_error_handler(error_handler)

    print("LinkedIn Reach Booster is gestart.")

    application.run_polling()


if __name__ == "__main__":
    main()


import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ==========================================================
# CONFIGURATIE
# ==========================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TIMEZONE = ZoneInfo("Europe/Amsterdam")
DATABASE = os.getenv("DATABASE_PATH", "linkedin_booster.db")

DAILY_LIMIT = 10
DAILY_SCAN_HOUR = 8

PROFILE_URL = (
    "https://www.linkedin.com/in/"
    "mark-bouwhuis-a5265727a/"
)

COMPANY_URL = "https://www.linkedin.com/company/raak-group/"

# Zoektermen gericht op jouw vakgebied.
SEARCH_TERMS = [
    "TIG welding Netherlands",
    "MIG MAG welding Netherlands",
    "welding inspection Netherlands",
    "welding coordinator IWT",
    "IWT IWI welding",
    "welding QC QA",
    "piping construction Netherlands",
    "pipe fitting Belgium",
    "industrial piping Benelux",
    "pressure equipment PED",
    "EN 13480 piping",
    "gas installation engineering",
]

# Woorden waarmee we controleren of een bericht relevant is.
RELEVANCE_WORDS = [
    "weld", "welding", "lassen", "lasser", "lastechniek",
    "tig", "mig", "mag", "piping", "pipe", "leidingbouw",
    "pijpfitter", "iwt", "iwi", "welding coordinator",
    "welding inspection", "qc", "qa", "inspection",
    "ped", "en 13480", "en 12732", "pressure equipment",
    "aardgas", "gas installation", "lasnaad", "lasinspectie",
    "materiaaltechniek", "engineering",
]

REGION_WORDS = [
    "netherlands", "nederland", "belgium", "belgië",
    "belgique", "luxembourg", "benelux",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ==========================================================
# DATABASE
# ==========================================================

def db_connection():
    return sqlite3.connect(DATABASE)


def init_database():
    with db_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS seen_posts (
                url TEXT PRIMARY KEY,
                title TEXT,
                seen_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)


def save_chat_id(chat_id):
    with db_connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("chat_id", str(chat_id)),
        )


def get_chat_id():
    with db_connection() as db:
        result = db.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("chat_id",),
        ).fetchone()

    return int(result[0]) if result else None


def already_seen(url):
    with db_connection() as db:
        result = db.execute(
            "SELECT url FROM seen_posts WHERE url = ?",
            (url,),
        ).fetchone()

    return result is not None


def save_post(url, title):
    with db_connection() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO seen_posts
            (url, title, seen_at)
            VALUES (?, ?, ?)
            """,
            (url, title, datetime.now(TIMEZONE).isoformat()),
        )


def get_seen_count():
    with db_connection() as db:
        result = db.execute(
            "SELECT COUNT(*) FROM seen_posts"
        ).fetchone()

    return result[0]


# ==========================================================
# LINKEDIN OPENBARE ZOEKPAGINA'S
# ==========================================================

def is_linkedin_post_url(url):
    parsed = urlparse(url)

    if parsed.netloc not in (
        "www.linkedin.com",
        "linkedin.com",
    ):
        return False

    path = parsed.path.lower()

    return (
        "/posts/" in path
        or "/feed/update/" in path
    )


def search_linkedin(search_term):
    search_url = (
        "https://www.linkedin.com/search/results/content/"
        "?keywords=" + quote(search_term)
    )

    try:
        response = requests.get(
            search_url,
            headers=HEADERS,
            timeout=15,
            allow_redirects=True,
        )

        if response.status_code != 200:
            logger.warning(
                "LinkedIn zoekpagina gaf status %s",
                response.status_code,
            )
            return []

        final_url = response.url.lower()

        if "authwall" in final_url or "login" in final_url:
            logger.warning("LinkedIn vraagt om inloggen.")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        results = []

        for link in soup.find_all("a", href=True):
            url = urljoin(
                "https://www.linkedin.com",
                link["href"],
            )

            if is_linkedin_post_url(url):
                clean_url = url.split("?")[0]

                if clean_url not in results:
                    results.append(clean_url)

        return results

    except requests.RequestException as error:
        logger.warning("Zoekfout: %s", error)
        return []


def read_public_post(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15,
            allow_redirects=True,
        )

        if response.status_code != 200:
            return None

        if "authwall" in response.url.lower():
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        title = ""
        description = ""

        og_title = soup.find(
            "meta",
            property="og:title",
        )

        og_description = soup.find(
            "meta",
            property="og:description",
        )

        meta_description = soup.find(
            "meta",
            attrs={"name": "description"},
        )

        if og_title:
            title = og_title.get("content", "")

        if og_description:
            description = og_description.get("content", "")
        elif meta_description:
            description = meta_description.get("content", "")

        # Extra tekst proberen te lezen uit openbare HTML.
        if len(description) < 40:
            main = soup.find("main")

            if main:
                description = main.get_text(
                    " ",
                    strip=True,
                )

        text = f"{title} {description}"
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) < 40:
            return None

        return {
            "url": url,
            "title": title or "LinkedIn bericht",
            "text": text[:2500],
        }

    except requests.RequestException as error:
        logger.warning("Bericht niet leesbaar: %s", error)
        return None


# ==========================================================
# RELEVANTIE
# ==========================================================

def relevance_score(text):
    text = text.lower()

    score = 0

    for word in RELEVANCE_WORDS:
        if word in text:
            score += 2

    for word in REGION_WORDS:
        if word in text:
            score += 1

    return score


def is_relevant(text):
    return relevance_score(text) >= 2


# ==========================================================
# REACTIES OPSTELLEN
# ==========================================================

def generate_comments(post_text):
    text = post_text.lower()

    comments = []

    if any(word in text for word in [
        "tig", "welding", "lassen", "lasnaad",
    ]):
        comments.append(
            "Interessant onderwerp! De kwaliteit van "
            "de las begint uiteindelijk al bij een "
            "goede voorbereiding en beheersing van "
            "het lasproces."
        )

    if any(word in text for word in [
        "inspection", "qc", "qa", "inspectie",
        "quality", "kwaliteit",
    ]):
        comments.append(
            "Mooi om te zien hoeveel aandacht hiervoor "
            "is. Goede kwaliteitscontrole tijdens het "
            "proces voorkomt vaak een hoop herstelwerk "
            "achteraf."
        )

    if any(word in text for word in [
        "piping", "pipe", "leidingbouw", "pijpfitter",
    ]):
        comments.append(
            "In de leidingbouw zit de kwaliteit vaak "
            "in de details. Een goede voorbereiding, "
            "maatvoering en traceerbaarheid maken "
            "echt het verschil."
        )

    if any(word in text for word in [
        "iwt", "iwi", "coordinator", "coördinator",
    ]):
        comments.append(
            "Een interessant vakgebied. Goede "
            "lascoördinatie vraagt om een combinatie "
            "van praktijkervaring, materiaal- en "
            "proceskennis."
        )

    if any(word in text for word in [
        "ped", "en 13480", "en 12732",
        "pressure equipment",
    ]):
        comments.append(
            "Belangrijk onderwerp binnen de "
            "drukapparatuur en leidingbouw. De "
            "afstemming tussen ontwerp, uitvoering "
            "en kwaliteitsborging blijft essentieel."
        )

    # Algemene reacties als er geen specifieke match is.
    general_comments = [
        "Mooi praktijkvoorbeeld uit de technische "
        "wereld. Juist dit soort kennisdeling is "
        "waardevol voor vakmensen.",

        "Interessant om te volgen. De combinatie "
        "van vakmanschap en technische kennis "
        "blijft belangrijk in onze branche.",

        "Goed dat hier aandacht aan wordt besteed. "
        "Er valt binnen dit vakgebied altijd iets "
        "te leren en te verbeteren.",
    ]

    for comment in general_comments:
        if len(comments) >= 3:
            break
        comments.append(comment)

    return comments[:3]


# ==========================================================
# SCAN
# ==========================================================

def scan_linkedin():
    logger.info("LinkedIn scan gestart.")

    found_urls = []

    for term in SEARCH_TERMS:
        if len(found_urls) >= 50:
            break

        urls = search_linkedin(term)

        for url in urls:
            if url not in found_urls:
                found_urls.append(url)

    posts = []

    for url in found_urls:
        if already_seen(url):
            continue

        post = read_public_post(url)

        if not post:
            continue

        if not is_relevant(post["text"]):
            continue

        post["score"] = relevance_score(post["text"])
        posts.append(post)

    posts.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    selected = posts[:DAILY_LIMIT]

    for post in selected:
        save_post(post["url"], post["title"])

    logger.info(
        "Scan klaar. %s relevante berichten gevonden.",
        len(selected),
    )

    return selected


# ==========================================================
# TELEGRAM
# ==========================================================

async def send_scan_results(bot, chat_id, posts):
    if not posts:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Vandaag heb ik geen nieuwe openbare "
                "LinkedIn-berichten kunnen vinden die "
                "voldoende aansluiten bij jouw vakgebied.\n\n"
                "Dit kan ook betekenen dat LinkedIn "
                "anoniem zoeken blokkeert."
            ),
        )
        return

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"Ik heb {len(posts)} relevante berichten "
            "gevonden voor jouw las- en pipingnetwerk."
        ),
    )

    for number, post in enumerate(posts, start=1):
        comments = generate_comments(post["text"])

        message = (
            f"{number}. {post['title']}\n\n"
            f"Waarom relevant:\n"
            f"{post['text'][:450]}\n\n"
            f"LinkedIn bericht:\n"
            f"{post['url']}\n\n"
            f"Suggestie 1:\n{comments[0]}\n\n"
            f"Suggestie 2:\n{comments[1]}\n\n"
            f"Suggestie 3:\n{comments[2]}"
        )

        await bot.send_message(
            chat_id=chat_id,
            text=message[:4000],
            disable_web_page_preview=True,
        )


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id
    save_chat_id(chat_id)

    await update.message.reply_text(
        "LinkedIn Reach Booster is actief.\n\n"
        "Ik zoek naar openbare berichten over lassen, "
        "piping, leidingbouw, IWT/IWI en QC/QA.\n\n"
        "Gebruik /scan om direct te zoeken.\n"
        "Gebruik /status voor de status."
    )


async def scan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id
    save_chat_id(chat_id)

    await update.message.reply_text(
        "Ik ga openbare LinkedIn-berichten zoeken. "
        "Dit kan even duren..."
    )

    posts = await asyncio.to_thread(scan_linkedin)

    await send_scan_results(
        context.bot,
        chat_id,
        posts,
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    count = get_seen_count()

    await update.message.reply_text(
        "LinkedIn Reach Booster status:\n\n"
        "Zoekgebied: Benelux\n"
        "Daglimiet: 10 berichten\n"
        "Automatische scan: dagelijks om 08:00\n"
        "Vakgebied: lassen, piping, QC/QA, IWT/IWI\n"
        f"Opgeslagen berichten: {count}\n"
        "Automatisch reageren: UIT\n"
        "Automatisch publiceren: UIT"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "Beschikbare commando's:\n\n"
        "/start - Bot activeren\n"
        "/scan - Direct een zoekronde starten\n"
        "/status - Status bekijken\n"
        "/help - Deze uitleg\n\n"
        "Je plaatst alle reacties zelf op LinkedIn."
    )


# ==========================================================
# DAGELIJKSE AUTOMATISCHE SCAN
# ==========================================================

async def daily_scheduler(application):
    while True:
        now = datetime.now(TIMEZONE)

        next_scan = now.replace(
            hour=DAILY_SCAN_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )

        if next_scan <= now:
            next_scan += timedelta(days=1)

        wait_seconds = (
            next_scan - now
        ).total_seconds()

        logger.info(
            "Volgende scan: %s",
            next_scan.isoformat(),
        )

        await asyncio.sleep(wait_seconds)

        chat_id = get_chat_id()

        if not chat_id:
            continue

        try:
            posts = await asyncio.to_thread(scan_linkedin)

            await send_scan_results(
                application.bot,
                chat_id,
                posts,
            )

        except Exception:
            logger.exception("Dagelijkse scan mislukt.")

            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "Er ging iets mis tijdens de "
                    "dagelijkse LinkedIn-scan."
                ),
            )


async def post_init(application):
    application.bot_data["scheduler_task"] = (
        asyncio.create_task(
            daily_scheduler(application)
        )
    )


async def post_shutdown(application):
    task = application.bot_data.get("scheduler_task")

    if task:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


# ==========================================================
# START
# ==========================================================

def main():
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN ontbreekt in Railway."
        )

    init_database()

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("scan", scan_command)
    )

    application.add_handler(
        CommandHandler("status", status_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    logger.info("LinkedIn Reach Booster gestart.")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

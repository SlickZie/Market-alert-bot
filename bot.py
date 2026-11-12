import os
import sqlite3
import logging
import requests
import yfinance as yf
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ==========================================
# 1. CONFIGURATION & SETTINGS
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
FREE_LIMIT = 2              # Free users can set max 2 alerts
PRO_PRICE_STARS = 150       # Price for PRO upgrade in Telegram Stars
ALERT_THRESHOLD = 3.0       # Alert when price changes by >= 3.0%
CHECK_INTERVAL = 600        # Check markets every 600 seconds (10 minutes)

# Available global assets (English labels)
CRYPTO_ASSETS = {
    "Bitcoin (BTC)": "bitcoin",
    "Ethereum (ETH)": "ethereum",
    "Solana (SOL)": "solana",
    "Ripple (XRP)": "ripple",
    "Cardano (ADA)": "cardano",
}

STOCK_ASSETS = {
    "S&P 500 Index": "^GSPC",
    "NASDAQ Composite": "^IXIC",
    "Dow Jones": "^DJI",
    "Gold (Futures)": "GC=F",
    "Nvidia (NVDA)": "NVDA",
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# 2. DATABASE SETUP (SQLite)
# ==========================================
def init_db():
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            is_pro INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            asset_name TEXT,
            asset_type TEXT,
            asset_code TEXT,
            last_price REAL DEFAULT 0,
            UNIQUE(user_id, asset_code)
        )
    """)
    conn.commit()
    conn.close()

def is_user_pro(user_id: int) -> bool:
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, is_pro) VALUES (?, 0)", (user_id,))
    conn.commit()
    c.execute("SELECT is_pro FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()[0]
    conn.close()
    return bool(result)

def set_user_pro(user_id: int):
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("UPDATE users SET is_pro = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_user_subs(user_id: int):
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("SELECT asset_name, asset_type, asset_code, last_price FROM subscriptions WHERE user_id = ?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

# ==========================================
# 3. SMART PRICE FETCHER (WITH CACHING)
# ==========================================
def fetch_all_prices() -> dict:
    """Fetches all prices in single batches to prevent API rate limits."""
    prices = {}

    # 1. Fetch Crypto via CoinGecko
    try:
        ids = ",".join(CRYPTO_ASSETS.values())
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
        response = requests.get(url, timeout=10).json()
        for name, cg_id in CRYPTO_ASSETS.items():
            if cg_id in response and "usd" in response[cg_id]:
                prices[cg_id] = float(response[cg_id]["usd"])
    except Exception as e:
        logger.error(f"Error fetching crypto prices: {e}")

    # 2. Fetch Stocks & Indices via Yahoo Finance
    for name, ticker in STOCK_ASSETS.items():
        try:
            data = yf.Ticker(ticker).fast_info
            if data and "lastPrice" in data and data["lastPrice"] is not None:
                prices[ticker] = float(data["lastPrice"])
        except Exception as e:
            logger.error(f"Error fetching stock {ticker}: {e}")

    return prices

# ==========================================
# 4. MENUS & TELEGRAM COMMANDS
# ==========================================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪙 Add Crypto", callback_data="menu_crypto"),
            InlineKeyboardButton("📈 Add Stock / Index", callback_data="menu_stock"),
        ],
        [
            InlineKeyboardButton("📊 My Watchlist & Prices", callback_data="my_alerts"),
            InlineKeyboardButton("🗑️ Remove Alert", callback_data="delete_menu"),
        ],
        [
            InlineKeyboardButton("⭐ Upgrade to PRO (Unlimited)", callback_data="buy_pro"),
        ],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pro_status = "⭐ PRO" if is_user_pro(user_id) else f"Free (max {FREE_LIMIT} alerts)"
    
    welcome_text = (
        f"👋 *Welcome to Global Market Alert Bot!*\n\n"
        f"Status: *{pro_status}*\n"
        f"Select what you want to track below. "
        f"You will automatically receive an instant notification when a price moves by more than *{ALERT_THRESHOLD}%*.\n\n"
        f"_Disclaimer: This bot provides automated market data only and does not constitute financial advice._"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 *Terms of Service:*\n"
        "1. This bot provides automated market data and price alerts.\n"
        "2. We do not offer financial or investment advice.\n"
        "3. Digital upgrades via Telegram Stars are activated instantly.",
        parse_mode="Markdown"
    )

async def paysupport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬 *Payment Support:*\n"
        "Have questions about payments with Telegram Stars? "
        "Reach out to our support team directly via Telegram.",
        parse_mode="Markdown"
    )

# ==========================================
# 5. BUTTON & INTERACTION HANDLER
# ==========================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "back_main":
        await query.edit_message_text("Main Menu:", reply_markup=main_menu_keyboard())

    elif data == "menu_crypto":
        buttons = [[InlineKeyboardButton(name, callback_data=f"add|crypto|{code}|{name}")] for name, code in CRYPTO_ASSETS.items()]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])
        await query.edit_message_text("Choose a *Crypto* to track:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "menu_stock":
        buttons = [[InlineKeyboardButton(name, callback_data=f"add|stock|{code}|{name}")] for name, code in STOCK_ASSETS.items()]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])
        await query.edit_message_text("Choose a *Stock / Index* to track:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("add|"):
        _, asset_type, asset_code, asset_name = data.split("|")
        current_subs = get_user_subs(user_id)

        # Check free tier limit
        if not is_user_pro(user_id) and len(current_subs) >= FREE_LIMIT:
            upgrade_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"⭐ Upgrade to PRO ({PRO_PRICE_STARS} Stars)", callback_data="buy_pro")],
                [InlineKeyboardButton("⬅️ Back", callback_data="back_main")]
            ])
            await query.edit_message_text(
                f"⚠️ You have reached the limit of *{FREE_LIMIT} free alerts*.\n\n"
                f"Upgrade to **PRO** to track unlimited crypto and stocks!",
                parse_mode="Markdown",
                reply_markup=upgrade_kb
            )
            return

        # Fetch current baseline price
        prices = fetch_all_prices()
        start_price = prices.get(asset_code, 0.0)

        conn = sqlite3.connect("bot_data.db")
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO subscriptions (user_id, asset_name, asset_type, asset_code, last_price) VALUES (?, ?, ?, ?, ?)",
            (user_id, asset_name, asset_type, asset_code, start_price)
        )
        conn.commit()
        conn.close()

        await query.edit_message_text(
            f"✅ *{asset_name}* added successfully!\n"
            f"Baseline price: `${start_price:,.2f}`\n\n"
            f"You will be alerted upon a {ALERT_THRESHOLD}% price movement.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

    elif data == "my_alerts":
        subs = get_user_subs(user_id)
        if not subs:
            await query.edit_message_text("You are not tracking any assets yet.", reply_markup=main_menu_keyboard())
            return

        prices = fetch_all_prices()
        lines = ["📊 *Your Active Watchlist:*\n"]
        for name, _, code, last_p in subs:
            curr_p = prices.get(code, last_p)
            lines.append(f"• *{name}*: `${curr_p:,.2f}`")

        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "delete_menu":
        subs = get_user_subs(user_id)
        if not subs:
            await query.edit_message_text("You have no active alerts to delete.", reply_markup=main_menu_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"❌ {name}", callback_data=f"del|{code}")] for name, _, code, _ in subs]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])
        await query.edit_message_text("Select an alert to remove:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("del|"):
        code = data.split("|")[1]
        conn = sqlite3.connect("bot_data.db")
        c = conn.cursor()
        c.execute("DELETE FROM subscriptions WHERE user_id = ? AND asset_code = ?", (user_id, code))
        conn.commit()
        conn.close()
        await query.edit_message_text("🗑️ Alert removed.", reply_markup=main_menu_keyboard())

    # ==========================================
    # 6. TELEGRAM STARS PAYMENT (PRO UPGRADE)
    # ==========================================
    elif data == "buy_pro":
        if is_user_pro(user_id):
            await query.edit_message_text("⭐ You already have an active PRO account!", reply_markup=main_menu_keyboard())
            return

        await context.bot.send_invoice(
            chat_id=user_id,
            title="PRO Access - Unlimited Price Alerts",
            description="Track unlimited global cryptocurrencies and stock market indices without restrictions.",
            payload="pro_upgrade_lifetime",
            provider_token="",  # Must be empty for Telegram Stars!
            currency="XTR",     # XTR = Telegram Stars currency code
            prices=[LabeledPrice("PRO Upgrade", PRO_PRICE_STARS)],
        )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mandatory confirmation within 10 seconds for Telegram Stars."""
    query = update.pre_checkout_query
    if query.invoice_payload != "pro_upgrade_lifetime":
        await query.answer(ok=False, error_message="Invoice verification failed.")
    else:
        await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activates PRO status immediately after payment."""
    user_id = update.effective_user.id
    set_user_pro(user_id)
    await update.message.reply_text(
        "🎉 *Payment successful! You are now PRO.*\n"
        "You can now add unlimited assets to your watchlist.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ==========================================
# 7. BACKGROUND TASK (24/7 MARKET CHECKER)
# ==========================================
async def check_markets_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Checking markets for price movements...")
    prices = fetch_all_prices()
    if not prices:
        return

    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()
    c.execute("SELECT id, user_id, asset_name, asset_code, last_price FROM subscriptions")
    all_subs = c.fetchall()

    for sub_id, user_id, name, code, last_price in all_subs:
        if code not in prices or last_price <= 0:
            continue

        current_price = prices[code]
        pct_change = ((current_price - last_price) / last_price) * 100

        if abs(pct_change) >= ALERT_THRESHOLD:
            direction = "🚀 SURGE" if pct_change > 0 else "🔻 DROP"
            msg = (
                f"{direction} *PRICE ALERT: {name}*\n\n"
                f"Movement: *{pct_change:+.2f}%*\n"
                f"Previous baseline: `${last_price:,.2f}`\n"
                f"Current price: `${current_price:,.2f}`\n\n"
                f"💡 _Tip: Always do your own research before trading._"
            )
            try:
                await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
                # Update baseline price so users aren't spammed continuously
                c.execute("UPDATE subscriptions SET last_price = ? WHERE id = ?", (current_price, sub_id))
            except Exception as e:
                logger.warning(f"Failed to send message to user {user_id}: {e}")

    conn.commit()
    conn.close()

# ==========================================
# 8. START BOT APPLICATION
# ==========================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("terms", terms_command))
    app.add_handler(CommandHandler("paysupport", paysupport_command))

    # Buttons & Payments
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # Background scheduler (runs every 10 minutes)
    app.job_queue.run_repeating(check_markets_job, interval=CHECK_INTERVAL, first=30)

    logger.info("Bot successfully started and running 24/7...")
    app.run_polling()

if __name__ == "__main__":
    main()

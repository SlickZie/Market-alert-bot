
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
# CONFIGURATION
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN ontbreekt in Railway Variables")

FREE_LIMIT = 2
PRO_PRICE_STARS = 150
ALERT_THRESHOLD = 3.0
CHECK_INTERVAL = 600

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


# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# ==========================================
# DATABASE
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

    c.execute(
        "INSERT OR IGNORE INTO users (user_id, is_pro) VALUES (?, 0)",
        (user_id,)
    )

    conn.commit()

    c.execute(
        "SELECT is_pro FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = c.fetchone()[0]
    conn.close()

    return bool(result)


def set_user_pro(user_id: int):
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()

    c.execute(
        "INSERT OR IGNORE INTO users (user_id, is_pro) VALUES (?, 0)",
        (user_id,)
    )

    c.execute(
        "UPDATE users SET is_pro = 1 WHERE user_id = ?",
        (user_id,)
    )

    conn.commit()
    conn.close()


def get_user_subs(user_id: int):
    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()

    c.execute("""
        SELECT asset_name, asset_type, asset_code, last_price
        FROM subscriptions
        WHERE user_id = ?
    """, (user_id,))

    rows = c.fetchall()
    conn.close()

    return rows


# ==========================================
# CRYPTO PRICE FETCHING
# ==========================================

def fetch_crypto_prices() -> dict:
    prices = {}

    try:
        ids = ",".join(CRYPTO_ASSETS.values())

        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={ids}&vs_currencies=usd"
        )

        response = requests.get(url, timeout=15)
        response.raise_for_status()

        data = response.json()

        for name, cg_id in CRYPTO_ASSETS.items():
            if cg_id in data and "usd" in data[cg_id]:
                prices[cg_id] = float(data[cg_id]["usd"])
                logger.info(
                    f"Crypto price received: {name} = {prices[cg_id]}"
                )
            else:
                logger.warning(f"No crypto price available for {name}")

    except Exception as e:
        logger.error(f"Crypto price error: {e}")

    return prices


# ==========================================
# STOCK AND INDEX PRICE FETCHING
# ==========================================

def fetch_stock_price(ticker: str):
    try:
        stock = yf.Ticker(ticker)

        # Eerst recente intraday-koersen proberen
        history = stock.history(
            period="1d",
            interval="1m",
            auto_adjust=False
        )

        if not history.empty:
            closes = history["Close"].dropna()

            if not closes.empty:
                price = float(closes.iloc[-1])

                if price > 0:
                    return price

        # Fallback: meest recente dagelijkse slotkoers
        history = stock.history(
            period="5d",
            interval="1d",
            auto_adjust=False
        )

        if not history.empty:
            closes = history["Close"].dropna()

            if not closes.empty:
                price = float(closes.iloc[-1])

                if price > 0:
                    return price

        logger.warning(f"No valid price for {ticker}")
        return None

    except Exception as e:
        logger.error(f"Error fetching {ticker}: {e}")
        return None


# ==========================================
# FETCH ALL MARKET PRICES
# ==========================================

def fetch_all_prices() -> dict:
    prices = {}

    # Crypto ophalen
    crypto_prices = fetch_crypto_prices()
    prices.update(crypto_prices)

    # Aandelen en indices ophalen
    for name, ticker in STOCK_ASSETS.items():
        price = fetch_stock_price(ticker)

        if price is not None:
            prices[ticker] = price
            logger.info(f"Market price received: {name} = {price}")
        else:
            logger.warning(f"Market price unavailable: {name} ({ticker})")

    return prices


# ==========================================
# TELEGRAM MAIN MENU
# ==========================================

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🪙 Add Crypto",
                callback_data="menu_crypto"
            ),
            InlineKeyboardButton(
                "📈 Add Stock / Index",
                callback_data="menu_stock"
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 My Watchlist & Prices",
                callback_data="my_alerts"
            ),
            InlineKeyboardButton(
                "🗑️ Remove Alert",
                callback_data="delete_menu"
            ),
        ],
        [
            InlineKeyboardButton(
                "⭐ Upgrade to PRO (Unlimited)",
                callback_data="buy_pro"
            ),
        ],
    ])


# ==========================================
# START COMMAND
# ==========================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    pro_status = (
        "⭐ PRO"
        if is_user_pro(user_id)
        else f"Free (max {FREE_LIMIT} alerts)"
    )

    welcome_text = (
        f"👋 *Welcome to Global Market Alert Bot!*\n\n"
        f"Status: *{pro_status}*\n\n"
        f"Select what you want to track below. "
        f"You will automatically receive an instant notification "
        f"when a price moves by more than *{ALERT_THRESHOLD}%*.\n\n"
        f"_Disclaimer: This bot provides automated market data only "
        f"and does not constitute financial advice._"
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )


# ==========================================
# TERMS COMMAND
# ==========================================

async def terms_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "📜 *Terms of Service:*\n"
        "1. This bot provides automated market data and price alerts.\n"
        "2. We do not offer financial or investment advice.\n"
        "3. Digital upgrades via Telegram Stars are activated instantly.",
        parse_mode="Markdown"
    )


# ==========================================
# PAYMENT SUPPORT
# ==========================================

async def paysupport_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "💬 *Payment Support:*\n\n"
        "Have questions about payments with Telegram Stars? "
        "Reach out to our support team directly via Telegram.",
        parse_mode="Markdown"
    )


# ==========================================
# BUTTON HANDLER
# ==========================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    # Back to main menu
    if data == "back_main":
        await query.edit_message_text(
            "Main Menu:",
            reply_markup=main_menu_keyboard()
        )

    # Crypto menu
    elif data == "menu_crypto":
        buttons = [
            [
                InlineKeyboardButton(
                    name,
                    callback_data=f"add|crypto|{code}|{name}"
                )
            ]
            for name, code in CRYPTO_ASSETS.items()
        ]

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="back_main"
            )
        ])

        await query.edit_message_text(
            "Choose a *Crypto* to track:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # Stocks and indices menu
    elif data == "menu_stock":
        buttons = [
            [
                InlineKeyboardButton(
                    name,
                    callback_data=f"add|stock|{code}|{name}"
                )
            ]
            for name, code in STOCK_ASSETS.items()
        ]

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="back_main"
            )
        ])

        await query.edit_message_text(
            "Choose a *Stock / Index* to track:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # Add asset
    elif data.startswith("add|"):
        _, asset_type, asset_code, asset_name = data.split("|")

        current_subs = get_user_subs(user_id)

        if (
            not is_user_pro(user_id)
            and len(current_subs) >= FREE_LIMIT
        ):
            upgrade_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        f"⭐ Upgrade to PRO ({PRO_PRICE_STARS} Stars)",
                        callback_data="buy_pro"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="back_main"
                    )
                ]
            ])

            await query.edit_message_text(
                f"⚠️ You have reached the limit of "
                f"*{FREE_LIMIT} free alerts*.\n\n"
                "Upgrade to PRO to track unlimited crypto and stocks!",
                parse_mode="Markdown",
                reply_markup=upgrade_kb
            )

            return

        # Fetch actual price before saving
        prices = fetch_all_prices()
        start_price = prices.get(asset_code)

        if start_price is None or start_price <= 0:
            await query.edit_message_text(
                f"⚠️ Could not retrieve a valid price for "
                f"*{asset_name}*.\n\n"
                "Please try again later.",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
            return

        conn = sqlite3.connect("bot_data.db")
        c = conn.cursor()

        c.execute("""
            INSERT OR REPLACE INTO subscriptions
            (user_id, asset_name, asset_type, asset_code, last_price)
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            asset_name,
            asset_type,
            asset_code,
            start_price
        ))

        conn.commit()
        conn.close()

        await query.edit_message_text(
            f"✅ *{asset_name}* added successfully!\n\n"
            f"Baseline price: `${start_price:,.2f}`\n\n"
            f"You will be alerted upon a "
            f"{ALERT_THRESHOLD}% price movement.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

    # Show watchlist
    elif data == "my_alerts":
        subs = get_user_subs(user_id)

        if not subs:
            await query.edit_message_text(
                "You are not tracking any assets yet.",
                reply_markup=main_menu_keyboard()
            )
            return

        prices = fetch_all_prices()
        lines = ["📊 *Your Active Watchlist:*\n"]

        for name, _, code, last_price in subs:
            curr_price = prices.get(code, last_price)

            lines.append(
                f"• *{name}*: `${curr_price:,.2f}`"
            )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

    # Delete menu
    elif data == "delete_menu":
        subs = get_user_subs(user_id)

        if not subs:
            await query.edit_message_text(
                "You have no active alerts to delete.",
                reply_markup=main_menu_keyboard()
            )
            return

        buttons = [
            [
                InlineKeyboardButton(
                    f"❌ {name}",
                    callback_data=f"del|{code}"
                )
            ]
            for name, _, code, _ in subs
        ]

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="back_main"
            )
        ])

        await query.edit_message_text(
            "Select an alert to remove:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # Delete asset
    elif data.startswith("del|"):
        code = data.split("|", 1)[1]

        conn = sqlite3.connect("bot_data.db")
        c = conn.cursor()

        c.execute(
            "DELETE FROM subscriptions "
            "WHERE user_id = ? AND asset_code = ?",
            (user_id, code)
        )

        conn.commit()
        conn.close()

        await query.edit_message_text(
            "🗑️ Alert removed.",
            reply_markup=main_menu_keyboard()
        )

    # Buy PRO
    elif data == "buy_pro":
        if is_user_pro(user_id):
            await query.edit_message_text(
                "⭐ You already have an active PRO account!",
                reply_markup=main_menu_keyboard()
            )
            return

        await context.bot.send_invoice(
            chat_id=user_id,
            title="PRO Access - Unlimited Price Alerts",
            description=(
                "Track unlimited global cryptocurrencies "
                "and stock market indices without restrictions."
            ),
            payload="pro_upgrade_lifetime",
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice("PRO Upgrade", PRO_PRICE_STARS)
            ],
        )


# ==========================================
# PAYMENT CALLBACKS
# ==========================================

async def precheckout_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.pre_checkout_query

    if query.invoice_payload != "pro_upgrade_lifetime":
        await query.answer(
            ok=False,
            error_message="Invoice verification failed."
        )
    else:
        await query.answer(ok=True)


async def successful_payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    set_user_pro(user_id)

    await update.message.reply_text(
        "🎉 *Payment successful! You are now PRO.*\n\n"
        "You can now add unlimited assets to your watchlist.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )


# ==========================================
# MARKET MONITORING
# ==========================================

async def check_markets_job(
    context: ContextTypes.DEFAULT_TYPE
):
    logger.info("Checking markets for price movements...")

    prices = fetch_all_prices()

    if not prices:
        logger.warning("No market prices received.")
        return

    conn = sqlite3.connect("bot_data.db")
    c = conn.cursor()

    c.execute("""
        SELECT id, user_id, asset_name, asset_code, last_price
        FROM subscriptions
    """)

    all_subs = c.fetchall()

    for sub_id, user_id, name, code, last_price in all_subs:

        if code not in prices or last_price <= 0:
            continue

        current_price = prices[code]

        pct_change = (
            (current_price - last_price) / last_price
        ) * 100

        if abs(pct_change) >= ALERT_THRESHOLD:

            direction = (
                "🚀 SURGE"
                if pct_change > 0
                else "🔻 DROP"
            )

            msg = (
                f"{direction} *PRICE ALERT: {name}*\n\n"
                f"Movement: *{pct_change:+.2f}%*\n"
                f"Previous baseline: `${last_price:,.2f}`\n"
                f"Current price: `${current_price:,.2f}`\n\n"
                f"💡 _Tip: Always do your own research before trading._"
            )

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=msg,
                    parse_mode="Markdown"
                )

                c.execute(
                    "UPDATE subscriptions SET last_price = ? WHERE id = ?",
                    (current_price, sub_id)
                )

                logger.info(
                    f"Alert sent: {name} ({pct_change:+.2f}%)"
                )

            except Exception as e:
                logger.warning(
                    f"Failed to send message to user {user_id}: {e}"
                )

    conn.commit()
    conn.close()


# ==========================================
# MAIN
# ==========================================

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("terms", terms_command))
    app.add_handler(CommandHandler("paysupport", paysupport_command))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(
        PreCheckoutQueryHandler(precheckout_callback)
    )

    app.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_callback
        )
    )

    app.job_queue.run_repeating(
        check_markets_job,
        interval=CHECK_INTERVAL,
        first=30
    )

    logger.info("Bot successfully started and running 24/7...")

    app.run_polling()


if __name__ == "__main__":
    main()

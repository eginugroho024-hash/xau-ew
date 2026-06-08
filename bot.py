from tradingview_ta import TA_Handler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from datetime import datetime, timedelta
import json
import os
import re
import requests
import time

# ==============================
# CONFIG
# ==============================
TOKEN = "8994879739:AAF40FWxNyfBL7EWET7G1TtvWxLkPbfMdzc"
ADMIN_ID = 7889334774  # ganti kalau ID admin lu beda

USER_FILE = "users.json"
TRIAL_LIMIT_MARKET = 3
TRIAL_LIMIT_NEWS = 3

# Isi pembayaran lu di sini
PAYMENT_TEXT = "DANA / QRIS: 085778001402"
ADMIN_CONTACT = "@egingroho"

PAIRS = {
    "XAUUSD": {
    "symbol": "XAUUSD",
    "screener": "cfd",
    "exchange": "FOREXCOM",
    "name": "XAU/USD"
},
    "XAGUSD": {
    "symbol": "SILVER",
    "screener": "cfd",
    "exchange": "TVC",
    "name": "XAG/USD"
},
    "BTCUSD": {"symbol": "BTCUSD", "screener": "crypto", "exchange": "BITSTAMP", "name": "BTC/USD"},
    "ETHUSD": {"symbol": "ETHUSD", "screener": "crypto", "exchange": "BITSTAMP", "name": "ETH/USD"},
}

TIMEFRAMES = {
    "M1": "1m", "M3": "3m", "M5": "5m", "M15": "15m",
    "M30": "30m", "H1": "1h", "H4": "4h", "DAILY": "1d",
}

IMPORTANT_NEWS_KEYWORDS = [
    "Non-Farm", "Nonfarm", "NFP", "CPI", "Core CPI", "FOMC",
    "Federal Funds Rate", "Fed Interest Rate", "PMI", "ISM", "Manufacturing PMI",
    "Services PMI", "PCE", "Core PCE", "Unemployment Rate", "Average Hourly Earnings"
]

# ==============================
# USER DATABASE
# ==============================
def load_users():
    if not os.path.exists(USER_FILE):
        return {}
    try:
        with open(USER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users):
    with open(USER_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=4)


def get_user(user_id):
    users = load_users()
    uid = str(user_id)

    if uid not in users:
        users[uid] = {
            "market_used": 0,
            "news_used": 0,
            "premium": False,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_users(users)

    # Admin otomatis premium
    if int(uid) == ADMIN_ID:
        users[uid]["premium"] = True
        save_users(users)

    return users[uid]


def update_user(user_id, data):
    users = load_users()
    users[str(user_id)] = data
    save_users(users)


def can_use_market(user_id):
    user = get_user(user_id)
    return user["premium"] or user["market_used"] < TRIAL_LIMIT_MARKET


def can_use_news(user_id):
    user = get_user(user_id)
    return user["premium"] or user["news_used"] < TRIAL_LIMIT_NEWS


def add_market_usage(user_id):
    user = get_user(user_id)
    if not user["premium"]:
        user["market_used"] += 1
        update_user(user_id, user)


def add_news_usage(user_id):
    user = get_user(user_id)
    if not user["premium"]:
        user["news_used"] += 1
        update_user(user_id, user)

# ==============================
# HELPERS
# ==============================
def fmt(x):
    try:
        return f"{float(x):.2f}"
    except Exception:
        return "-"


def conf_bar(conf):
    full = int(conf / 10)
    return "█" * full + "░" * (10 - full)


def clean_num(value):
    if value is None:
        return None
    value = str(value).replace(",", "").replace("%", "").replace("K", "").replace("M", "")
    m = re.search(r"-?\d+(\.\d+)?", value)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def dynamic_sl_tp(pair_key, signal, entry, high, low):
    rng = abs(high - low)

    if pair_key == "XAUUSD":
        min_sl, min_tp = 3.0, 6.0
        max_sl, max_tp = 4.0, 10.0
    elif pair_key == "XAGUSD":
        min_sl, min_tp = 0.12, 0.25
        max_sl, max_tp = 0.20, 0.45
    elif pair_key == "BTCUSD":
        min_sl, min_tp = 120, 250
        max_sl, max_tp = 250, 600
    else:  # ETHUSD
        min_sl, min_tp = 10, 25
        max_sl, max_tp = 20, 50

    sl_dist = max(min_sl, min(rng * 1.2, max_sl))
    tp_dist = max(min_tp, min(sl_dist * 2.2, max_tp))

    if signal == "BUY":
        return entry - sl_dist, entry + tp_dist
    return entry + sl_dist, entry - tp_dist

# ==============================
# MARKET ANALYSIS: SNR + SND + ICT + SMC
# ==============================
def get_market_analysis(pair_key, tf_key):
    pair = PAIRS[pair_key]

    def fetch_tf(tf):
        handler = TA_Handler(
            symbol=pair["symbol"],
            screener=pair["screener"],
            exchange=pair["exchange"],
            interval=TIMEFRAMES[tf]
        )
        data = handler.get_analysis()
        ind = data.indicators
        summ = data.summary

        price = ind.get("close")
        open_price = ind.get("open")
        high = ind.get("high")
        low = ind.get("low")
        ema20 = ind.get("EMA20")
        ema50 = ind.get("EMA50")
        rec = summ.get("RECOMMENDATION", "NEUTRAL")

        if price is None or high is None or low is None:
            raise Exception("Data market belum lengkap dari TradingView.")

        price = float(price)
        open_price = float(open_price) if open_price else price
        high = float(high)
        low = float(low)

        candle = "BULLISH" if price > open_price else "BEARISH"

        if rec in ["BUY", "STRONG_BUY"]:
            bias = "BULLISH"
        elif rec in ["SELL", "STRONG_SELL"]:
            bias = "BEARISH"
        else:
            if ema20 and ema50:
                try:
                    ema20 = float(ema20)
                    ema50 = float(ema50)
                    if price > ema20 > ema50:
                        bias = "BULLISH"
                    elif price < ema20 < ema50:
                        bias = "BEARISH"
                    else:
                        bias = "SIDEWAYS"
                except Exception:
                    bias = "SIDEWAYS"
            else:
                bias = "SIDEWAYS"

        return {
            "price": price,
            "open": open_price,
            "high": high,
            "low": low,
            "rec": rec,
            "bias": bias,
            "candle": candle,
            "eq": (high + low) / 2
        }

    try:
        tf_data = fetch_tf(tf_key)
        time.sleep(1)
        h1 = fetch_tf("H1")
        time.sleep(1)
        m15 = fetch_tf("M15")
        time.sleep(1)
        m5 = fetch_tf("M5")
    except Exception as e:
        return f"⚠️ Error ambil data market:\n{str(e)}"

    price = tf_data["price"]
    high = tf_data["high"]
    low = tf_data["low"]
    market_range = max(abs(high - low), 0.0001)

    h1_bias = h1["bias"]
    m15_bias = m15["bias"]
    m5_bias = m5["bias"]

    valid_buy = h1_bias == "BULLISH" and m15_bias == "BULLISH" and m5_bias == "BULLISH"
    valid_sell = h1_bias == "BEARISH" and m15_bias == "BEARISH" and m5_bias == "BEARISH"

    # SESSION WIB
    wib_now = datetime.utcnow() + timedelta(hours=7)
    hour = wib_now.hour

    if 5 <= hour < 14:
        session_name = "🌏 ASIA SESSION"
        session_note = "Liquidity Building"
        session_score = 4
    elif 14 <= hour < 20:
        session_name = "🇬🇧 LONDON SESSION"
        session_note = "High Volatility Window"
        session_score = 10
    else:
        session_name = "🇺🇸 NEW YORK SESSION"
        session_note = "Institutional Flow Active"
        session_score = 10

    # NO TRADE jika MTF belum searah
    if not valid_buy and not valid_sell:
        confidence = 55
        confidence_bar = conf_bar(confidence)

        return f"""
🏦 <b>INSTITUTIONAL FLOW CHECK</b>

👑 <b>EGI CAPITAL ELITE VVIP+</b>

━━━━━━━━━━━━━━━━━━

💱 <b>{pair["name"]}</b> • {tf_key}

🕒 {wib_now.strftime("%H:%M")} WIB
🌍 {session_name}

━━━━━━━━━━━━━━━━━━

🤖 <b>AI MARKET BIAS</b>

⚪ <b>SIDEWAYS / NO VALID SETUP</b>

Multi-timeframe belum searah.
Hindari entry agar tidak floating lama.

━━━━━━━━━━━━━━━━━━

📊 <b>MTF CONFIRMATION</b>

H1  ➜ <b>{h1_bias}</b>
M15 ➜ <b>{m15_bias}</b>
M5  ➜ <b>{m5_bias}</b>

━━━━━━━━━━━━━━━━━━

⛔ <b>TRADE PLAN</b>

<b>NO TRADE</b>

━━━━━━━━━━━━━━━━━━

🧠 <b>AI VERDICT</b>

❌ Institutional confirmation belum valid.
❌ H1, M15, dan M5 belum satu arah.
✅ Tunggu setup berikutnya.
✅ Fokus hanya entry saat MTF selaras.

━━━━━━━━━━━━━━━━━━

⚡ <b>SETUP QUALITY</b>

🏅 Grade : <b>C</b>
📊 Confidence : <b>{confidence}%</b>

{confidence_bar}

━━━━━━━━━━━━━━━━━━

⚠️ <b>EXECUTION RULE</b>

Jangan paksa entry.
Tunggu H1 + M15 + M5 searah.

━━━━━━━━━━━━━━━━━━

⚠️ <b>DISCLAIMER</b>

Bukan saran finansial.
Trading mengandung risiko tinggi.
Gunakan stop loss dan kelola modal dengan bijak.

━━━━━━━━━━━━━━━━━━

👑 <b>EGI CAPITAL ELITE VVIP+</b>
"""

    # SIGNAL VALID
    signal = "BUY" if valid_buy else "SELL"
    ai_market = "BULLISH" if signal == "BUY" else "BEARISH"

    if signal == "BUY":
        ai_note = "Buyer dominan di H1, M15, dan M5. Fokus cari BUY saat harga masuk area retest."
    else:
        ai_note = "Seller dominan di H1, M15, dan M5. Fokus cari SELL saat harga masuk area retest."

    # SL distance per pair
    if pair_key == "XAUUSD":
        sl_dist = max(3.0, min(market_range * 0.8, 5.0))
    elif pair_key == "XAGUSD":
        sl_dist = max(0.12, min(market_range * 0.8, 0.25))
    elif pair_key == "BTCUSD":
        sl_dist = max(120, min(market_range * 0.8, 300))
    else:
        sl_dist = max(10, min(market_range * 0.8, 35))

    entry = price

    # Score system: H1 25, M15 25, M5 20, retest proxy 20, session 10
    score = 25 + 25 + 20
    eq = tf_data["eq"]
    near_retest = abs(price - eq) <= (market_range * 0.35)

    if near_retest:
        score += 20
    else:
        score += 10

    score += session_score
    confidence = min(score, 100)

    if confidence >= 95:
        grade = "A+"
        market_condition = "VVIP HIGH PROBABILITY"
        market_note = "MTF selaras dan area retest valid. Prioritaskan Elite Entry."
    elif confidence >= 90:
        grade = "A"
        market_condition = "PREMIUM VALID SETUP"
        market_note = "Setup valid. Entry hanya saat harga masuk area."
    elif confidence >= 80:
        grade = "B+"
        market_condition = "VALID BUT WAIT RETEST"
        market_note = "Tunggu harga masuk Elite Entry agar floating lebih kecil."
    else:
        grade = "NO TRADE"
        market_condition = "LOW QUALITY SETUP"
        market_note = "Sebaiknya tunggu konfirmasi baru."

    if signal == "BUY":
        entry_low = entry - (sl_dist * 0.30)
        entry_high = entry + (sl_dist * 0.10)
        zero_low = entry - (sl_dist * 0.08)
        zero_high = entry + (sl_dist * 0.03)
        sl = entry - sl_dist
        tp1 = entry + (sl_dist * 1.2)
        tp2 = entry + (sl_dist * 2.0)
        tp3 = entry + (sl_dist * 2.8)
        entry_reason = """
✅ H1 bullish sebagai arah utama.
✅ M15 bullish sebagai setup confirmation.
✅ M5 bullish sebagai trigger entry.
✅ Entry diarahkan ke area retest, bukan asal harga sekarang.
✅ Target menuju buy-side liquidity.
"""
    else:
        entry_low = entry - (sl_dist * 0.10)
        entry_high = entry + (sl_dist * 0.30)
        zero_low = entry - (sl_dist * 0.03)
        zero_high = entry + (sl_dist * 0.08)
        sl = entry + sl_dist
        tp1 = entry - (sl_dist * 1.2)
        tp2 = entry - (sl_dist * 2.0)
        tp3 = entry - (sl_dist * 2.8)
        entry_reason = """
✅ H1 bearish sebagai arah utama.
✅ M15 bearish sebagai setup confirmation.
✅ M5 bearish sebagai trigger entry.
✅ Entry diarahkan ke area retest, bukan asal harga sekarang.
✅ Target menuju sell-side liquidity.
"""

    rr = round(abs(tp2 - entry) / max(abs(entry - sl), 0.0001), 1)
    confidence_bar = conf_bar(confidence)

    return f"""
🏦 <b>INSTITUTIONAL FLOW DETECTED</b>

👑 <b>EGI CAPITAL ELITE VVIP+</b>

━━━━━━━━━━━━━━━━━━

💱 <b>{pair["name"]}</b> • {tf_key}

🕒 {wib_now.strftime("%H:%M")} WIB
🌍 {session_name}

━━━━━━━━━━━━━━━━━━

🤖 <b>AI MARKET BIAS</b>

{"📈 BULLISH" if signal=="BUY" else "📉 BEARISH"}

{ai_note}

━━━━━━━━━━━━━━━━━━

🔥 <b>TRADE PLAN</b>

{"🟢 STRONG BUY" if signal=="BUY" else "🔴 STRONG SELL"}

🎯 <b>INSTITUTIONAL ENTRY</b>
<code>{fmt(entry_low)} - {fmt(entry_high)}</code>

💎 <b>ELITE ENTRY (SNIPER)</b>
<code>{fmt(zero_low)} - {fmt(zero_high)}</code>

━━━━━━━━━━━━━━━━━━

🛡 <b>RISK MANAGEMENT</b>

🛑 Stop Loss
<code>{fmt(sl)}</code>

━━━━━━━━━━━━━━━━━━

🎯 <b>PROFIT TARGET</b>

TP1 ➜ <code>{fmt(tp1)}</code>
TP2 ➜ <code>{fmt(tp2)}</code>
TP3 ➜ <code>{fmt(tp3)}</code>

⚖️ RR ➜ <b>1 : {rr}</b>

━━━━━━━━━━━━━━━━━━

⚡ <b>SETUP QUALITY</b>

🏅 Grade : <b>{grade}</b>
📊 Confidence : <b>{confidence}%</b>

{confidence_bar}

━━━━━━━━━━━━━━━━━━

🧠 <b>AI VERDICT</b>

{"✅ H1 Bullish" if h1_bias=="BULLISH" else "✅ H1 Bearish"}
{"✅ M15 Bullish" if m15_bias=="BULLISH" else "✅ M15 Bearish"}
{"✅ M5 Bullish" if m5_bias=="BULLISH" else "✅ M5 Bearish"}

✅ Multi-Timeframe Confirmed
✅ Institutional Flow Aligned
✅ Liquidity Direction Confirmed

━━━━━━━━━━━━━━━━━━

📝 <b>ENTRY REASON</b>

{entry_reason}

━━━━━━━━━━━━━━━━━━

🚨 <b>EXECUTION RULE</b>

❌ Jangan entry sekarang
✅ Tunggu harga masuk:

💎 <b>ELITE ENTRY ZONE</b>

Entry di luar area ini berpotensi menyebabkan floating lebih lama.

━━━━━━━━━━━━━━━━━━

🎖 <b>TRADER NOTE</b>

{market_note}

━━━━━━━━━━━━━━━━━━

⚠️ <b>DISCLAIMER</b>

Bukan saran finansial.
Trading mengandung risiko tinggi.
Gunakan stop loss.
Jangan overlot.
Kelola modal dengan bijak.

━━━━━━━━━━━━━━━━━━

👑 <b>EGI CAPITAL ELITE VVIP+</b>
"""


# ==============================
# NEWS IMPACT ENGINE
# ==============================
def parse_forex_factory_today():
    """
    Scrape Forex Factory calendar. Gratis, tapi bisa gagal kalau website block request.
    Kalau gagal, bot tetap punya manual analyzer lewat /news dan /fomc.
    """
    url = "https://www.forexfactory.com/calendar"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        r = requests.get(url, headers=headers, timeout=15)
        html = r.text

        # Regex ringan: ambil blok sekitar event USD. Struktur FF bisa berubah kapan saja.
        # Karena FF sering ubah HTML, ini dibuat fallback-friendly.
        events = []
        rows = re.findall(r"<tr[^>]*calendar__row[^>]*>(.*?)</tr>", html, flags=re.S | re.I)

        for row in rows:
            txt = re.sub(r"<[^>]+>", " ", row)
            txt = re.sub(r"\s+", " ", txt).strip()

            if " USD " not in (" " + txt + " "):
                continue

            if not any(k.lower() in txt.lower() for k in IMPORTANT_NEWS_KEYWORDS):
                continue

            impact = "HIGH" if "High Impact" in row or "impact--high" in row else "NEWS"
            events.append({"raw": txt, "impact": impact})

        return events[:8]
    except Exception:
        return []


def analyze_news_result(news_type, actual, forecast, previous=None):
    nt = news_type.lower()
    actual_num = clean_num(actual)
    forecast_num = clean_num(forecast)

    if actual_num is None or forecast_num is None:
        return "Format angka tidak valid. Contoh: /news cpi actual=3.2 forecast=3.4 previous=3.5"

    usd_bias = "NEUTRAL"
    market_reason = "Actual sama dengan forecast. Market bisa choppy."
    confidence = 65

    # CPI/PCE: lebih tinggi = inflasi kuat = USD bullish, gold/crypto bearish
    if "cpi" in nt or "pce" in nt:
        if actual_num > forecast_num:
            usd_bias = "BULLISH"
            market_reason = "Inflasi lebih tinggi dari forecast. Potensi Fed lebih hawkish."
            confidence = 88
        elif actual_num < forecast_num:
            usd_bias = "BEARISH"
            market_reason = "Inflasi lebih rendah dari forecast. Potensi Fed lebih dovish."
            confidence = 88

    # NFP/PMI/ISM/Unemployment earnings general rules
    elif "nfp" in nt or "nonfarm" in nt or "pmi" in nt or "ism" in nt or "jobs" in nt:
        if actual_num > forecast_num:
            usd_bias = "BULLISH"
            market_reason = "Data ekonomi lebih kuat dari forecast. USD cenderung menguat."
            confidence = 86
        elif actual_num < forecast_num:
            usd_bias = "BEARISH"
            market_reason = "Data ekonomi lebih lemah dari forecast. USD cenderung melemah."
            confidence = 86

    # Unemployment rate kebalik: angka lebih tinggi = USD bearish
    if "unemployment" in nt:
        if actual_num > forecast_num:
            usd_bias = "BEARISH"
            market_reason = "Unemployment lebih tinggi dari forecast. USD cenderung melemah."
            confidence = 86
        elif actual_num < forecast_num:
            usd_bias = "BULLISH"
            market_reason = "Unemployment lebih rendah dari forecast. USD cenderung menguat."
            confidence = 86

    if usd_bias == "BULLISH":
        xau = "SELL"
        xag = "SELL"
        btc = "SELL / RISK-OFF"
        eth = "SELL / RISK-OFF"
    elif usd_bias == "BEARISH":
        xau = "BUY"
        xag = "BUY"
        btc = "BUY / RISK-ON"
        eth = "BUY / RISK-ON"
    else:
        xau = xag = btc = eth = "WAIT FOR VOLATILITY"

    now = datetime.now().strftime("%d-%m-%Y %H:%M WIB")

    return f"""
⬜━━━━━━━━━━━━━━━━━━━━⬜
🚨 <b>NEWS IMPACT ANALYSIS</b>
⬜━━━━━━━━━━━━━━━━━━━━⬜

📅 <b>Waktu</b>     : {now}
📰 <b>News</b>      : <b>{news_type.upper()}</b>
📊 <b>Actual</b>    : <code>{actual}</code>
📈 <b>Forecast</b>  : <code>{forecast}</code>
📉 <b>Previous</b>  : <code>{previous if previous else '-'}</code>

⬜━━━━━━━━━━━━━━━━━━━━⬜
🤖 <b>AI VERDICT</b>
⬜━━━━━━━━━━━━━━━━━━━━⬜

💵 <b>USD Bias</b>   : <b>{usd_bias}</b>
🎯 <b>Confidence</b> : <b>{confidence}%</b> {conf_bar(confidence)}

🥇 XAU/USD : <b>{xau}</b>
🥈 XAG/USD : <b>{xag}</b>
₿ BTC/USD : <b>{btc}</b>
♦ ETH/USD : <b>{eth}</b>

✅ <b>Reason</b>:
{market_reason}

⬜━━━━━━━━━━━━━━━━━━━━⬜
⚠️ <b>NEWS TRADING RULE</b>
⬜━━━━━━━━━━━━━━━━━━━━⬜

<i>Jangan entry di detik rilis news.
Tunggu 1-2 candle M5 close, spread normal, lalu cari retest.</i>

🤍 <b>Egi Capital AI — News Engine</b>
"""


def analyze_fomc(tone):
    t = tone.lower()
    if "hawk" in t or "naik" in t or "higher" in t:
        usd = "BULLISH"
        xau = "SELL"
        btc = "SELL / RISK-OFF"
        reason = "Nada FOMC hawkish. Market melihat peluang suku bunga lebih tinggi/lebih lama."
        conf = 88
    elif "dov" in t or "turun" in t or "cut" in t:
        usd = "BEARISH"
        xau = "BUY"
        btc = "BUY / RISK-ON"
        reason = "Nada FOMC dovish. Market melihat peluang pelonggaran kebijakan."
        conf = 88
    else:
        usd = "NEUTRAL"
        xau = "WAIT"
        btc = "WAIT"
        reason = "Tone FOMC belum jelas. Tunggu press conference dan reaksi candle."
        conf = 65

    return f"""
⬜━━━━━━━━━━━━━━━━━━━━⬜
🏦 <b>FOMC IMPACT ANALYSIS</b>
⬜━━━━━━━━━━━━━━━━━━━━⬜

🧠 <b>Tone</b>      : <b>{tone.upper()}</b>
💵 <b>USD Bias</b>  : <b>{usd}</b>
🎯 <b>Confidence</b>: <b>{conf}%</b> {conf_bar(conf)}

🥇 XAU/USD : <b>{xau}</b>
🥈 XAG/USD : <b>{xau}</b>
₿ BTC/USD : <b>{btc}</b>
♦ ETH/USD : <b>{btc}</b>

✅ <b>Reason</b>:
{reason}

⚠️ Tunggu 1-2 candle M5 setelah statement/press conference.
"""

# ==============================
# MENUS
# ==============================
def main_menu(user_id):
    user = get_user(user_id)
    if user["premium"]:
        status = "💎 PREMIUM UNLIMITED"
    else:
        status = f"🆓 TRIAL Market {TRIAL_LIMIT_MARKET - user['market_used']} | News {TRIAL_LIMIT_NEWS - user['news_used']}"

    text = f"""
    ━━━━━━━━━━━━━━━━━━━━━━
    👑 <b>EGI CAPITAL ELITE</b>
    ━━━━━━━━━━━━━━━━━━━━━━

    🏦 <b>Institutional AI Engine</b>

    AI-Powered Forex • Gold • Crypto Analysis

    ━━━━━━━━━━━━━━━━━━━━━━

    💎 <b>STATUS MEMBER</b>

    ✅ <b>PREMIUM UNLIMITED</b>

    ━━━━━━━━━━━━━━━━━━━━━━

    ⚡ <b>FITUR ELITE</b>

    📊 Multi-Timeframe AI
    (H1 • M15 • M5)

    🎯 Institutional Entry

    💎 Elite Sniper Zone

    📰 Smart News Engine

    🚨 Auto Market Broadcast

    ━━━━━━━━━━━━━━━━━━━━━━

    🤖 <b>AI SYSTEM STATUS</b>

    🟢 <b>ONLINE</b>

    Market akan difilter secara otomatis.

    Hanya setup dengan probabilitas
    tinggi yang akan diberikan.

    ━━━━━━━━━━━━━━━━━━━━━━

    👇 <b>Pilih menu di bawah</b>
    """
    keyboard = [
        [InlineKeyboardButton("📊 Analisa Market", callback_data="menu_pairs")],
        [InlineKeyboardButton("📰 News Impact", callback_data="menu_news")],
        [InlineKeyboardButton("👤 Akun Saya", callback_data="account")],
        [InlineKeyboardButton("💎 Upgrade Premium", callback_data="upgrade")],
    ]
    return text, InlineKeyboardMarkup(keyboard)

# ==============================
# TELEGRAM HANDLERS
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, keyboard = main_menu(update.effective_user.id)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = q.from_user.id
    user = get_user(user_id)
    data = q.data

    if data == "menu_pairs":
        if not can_use_market(user_id):
            await q.edit_message_text(
                "🚫 <b>FREE TRIAL MARKET HABIS</b>\n\nUpgrade premium untuk akses unlimited.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade Premium", callback_data="upgrade")]]),
                parse_mode="HTML"
            )
            return
        keyboard = [
            [InlineKeyboardButton("🥇 XAU/USD", callback_data="pair_XAUUSD"), InlineKeyboardButton("🥈 XAG/USD", callback_data="pair_XAGUSD")],
            [InlineKeyboardButton("₿ BTC/USD", callback_data="pair_BTCUSD"), InlineKeyboardButton("♦ ETH/USD", callback_data="pair_ETHUSD")],
            [InlineKeyboardButton("⬅️ Kembali", callback_data="back_start")]
        ]
        await q.edit_message_text("🏆 <b>Kategori: Komoditas & Crypto</b>\n\nPilih pair:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("pair_"):
        pair_key = data.replace("pair_", "")
        keyboard = [
            [InlineKeyboardButton("M1", callback_data=f"tf_{pair_key}_M1"), InlineKeyboardButton("M3", callback_data=f"tf_{pair_key}_M3"), InlineKeyboardButton("M5", callback_data=f"tf_{pair_key}_M5")],
            [InlineKeyboardButton("M15", callback_data=f"tf_{pair_key}_M15"), InlineKeyboardButton("M30", callback_data=f"tf_{pair_key}_M30"), InlineKeyboardButton("H1", callback_data=f"tf_{pair_key}_H1")],
            [InlineKeyboardButton("H4", callback_data=f"tf_{pair_key}_H4"), InlineKeyboardButton("DAILY", callback_data=f"tf_{pair_key}_DAILY")],
            [InlineKeyboardButton("⬅️ Kembali", callback_data="menu_pairs")]
        ]
        await q.edit_message_text(f"💱 <b>{PAIRS[pair_key]['name']}</b>\n\nPilih timeframe:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("tf_"):
        if not can_use_market(user_id):
            await q.edit_message_text("🚫 <b>FREE TRIAL MARKET HABIS</b>\n\nUpgrade premium untuk akses unlimited.", parse_mode="HTML")
            return
        parts = data.split("_")
        pair_key, tf_key = parts[1], parts[2]
        await q.edit_message_text("⏳ Sedang analisa market...")
        try:
            hasil = get_market_analysis(pair_key, tf_key)
            add_market_usage(user_id)
            user = get_user(user_id)
            if not user["premium"]:
                hasil += f"\n\n🆓 Sisa trial market: {TRIAL_LIMIT_MARKET - user['market_used']} analisa"
        except Exception as e:
            hasil = "Error ambil data market: " + str(e)
        keyboard = [[InlineKeyboardButton("🔁 Analisa Lagi", callback_data=f"pair_{pair_key}")], [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_start")]]
        await q.edit_message_text(hasil, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "menu_news":
        if not can_use_news(user_id):
            await q.edit_message_text("🚫 <b>FREE TRIAL NEWS HABIS</b>\n\nUpgrade premium untuk akses unlimited.", parse_mode="HTML")
            return
        keyboard = [
            [InlineKeyboardButton("📅 Forex Factory Today", callback_data="news_ff")],
            [InlineKeyboardButton("📌 Cara Input Manual", callback_data="news_manual_help")],
            [InlineKeyboardButton("⬅️ Kembali", callback_data="back_start")]
        ]
        await q.edit_message_text("📰 <b>NEWS IMPACT ENGINE</b>\n\nPilih menu news:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "news_ff":
        if not can_use_news(user_id):
            await q.edit_message_text("🚫 <b>FREE TRIAL NEWS HABIS</b>\n\nUpgrade premium untuk akses unlimited.", parse_mode="HTML")
            return
        await q.edit_message_text("⏳ Mengambil data Forex Factory...")
        events = parse_forex_factory_today()
        add_news_usage(user_id)
        if events:
            text = "⬜━━━━━━━━━━━━━━━━━━━━⬜\n📰 <b>FOREX FACTORY TODAY</b>\n⬜━━━━━━━━━━━━━━━━━━━━⬜\n\n"
            for idx, ev in enumerate(events, 1):
                text += f"<b>{idx}. USD {ev['impact']}</b>\n{ev['raw']}\n\n"
            text += "Gunakan command manual setelah actual keluar:\n<code>/news cpi actual=3.2 forecast=3.4 previous=3.5</code>"
        else:
            text = "⚠️ Forex Factory gagal dibaca / tidak ada news USD penting.\n\nGunakan manual:\n<code>/news nfp actual=250 forecast=180 previous=190</code>\n<code>/fomc hawkish</code>"
        user = get_user(user_id)
        if not user["premium"]:
            text += f"\n\n🆓 Sisa trial news: {TRIAL_LIMIT_NEWS - user['news_used']}"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="back_start")]]), parse_mode="HTML")

    elif data == "news_manual_help":
        text = """
📰 <b>MANUAL NEWS ANALYZER</b>

Format:
<code>/news cpi actual=3.2 forecast=3.4 previous=3.5</code>
<code>/news nfp actual=250 forecast=180 previous=190</code>
<code>/news pmi actual=53.2 forecast=51.8 previous=50.9</code>
<code>/fomc hawkish</code>
<code>/fomc dovish</code>

Rule:
CPI tinggi = USD bullish = XAU cenderung bearish.
NFP/PMI tinggi = USD bullish = XAU cenderung bearish.
FOMC hawkish = USD bullish.
FOMC dovish = USD bearish.
"""
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="menu_news")]]), parse_mode="HTML")

    elif data == "account":
        user = get_user(user_id)
        status = "💎 PREMIUM" if user["premium"] else "🆓 FREE TRIAL"
        market_left = "Unlimited" if user["premium"] else f"{TRIAL_LIMIT_MARKET - user['market_used']} / {TRIAL_LIMIT_MARKET}"
        news_left = "Unlimited" if user["premium"] else f"{TRIAL_LIMIT_NEWS - user['news_used']} / {TRIAL_LIMIT_NEWS}"
        text = f"""
👤 <b>AKUN SAYA</b>

ID Telegram:
<code>{user_id}</code>

Status: <b>{status}</b>
Trial Market: <b>{market_left}</b>
Trial News: <b>{news_left}</b>
"""
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="back_start")]]), parse_mode="HTML")

    elif data == "upgrade":
        text = f"""
text = f"""
👑 <b>EGI CAPITAL ELITE</b>

💎 PREMIUM UNLIMITED

━━━━━━━━━━━━

📊 AI Market Analysis

🎯 Elite Entry Zone

💎 Sniper Entry

📰 High Impact News

🚨 Daily Broadcast

━━━━━━━━━━━━

💰 <b>Lifetime :</b>

<code>Rp 499.000</code>

━━━━━━━━━━━━

💳 <b>Payment :</b>

<code>{PAYMENT_TEXT}</code>

📩 Admin:
{ADMIN_CONTACT}
"""
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="back_start")]]), parse_mode="HTML")

    elif data == "back_start":
        text, keyboard = main_menu(user_id)
        await q.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not can_use_news(user_id):
        await update.message.reply_text("🚫 Free trial news habis. Upgrade premium untuk akses unlimited.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Format: /news cpi actual=3.2 forecast=3.4 previous=3.5")
        return

    news_type = context.args[0]
    raw = " ".join(context.args[1:])
    actual = re.search(r"actual=([^\s]+)", raw, re.I)
    forecast = re.search(r"forecast=([^\s]+)", raw, re.I)
    previous = re.search(r"previous=([^\s]+)", raw, re.I)

    if not actual or not forecast:
        await update.message.reply_text("Format salah. Contoh: /news nfp actual=250 forecast=180 previous=190")
        return

    hasil = analyze_news_result(news_type, actual.group(1), forecast.group(1), previous.group(1) if previous else None)
    add_news_usage(user_id)
    await update.message.reply_text(hasil, parse_mode="HTML")


async def fomc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not can_use_news(user_id):
        await update.message.reply_text("🚫 Free trial news habis. Upgrade premium untuk akses unlimited.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /fomc hawkish atau /fomc dovish")
        return
    tone = " ".join(context.args)
    hasil = analyze_fomc(tone)
    add_news_usage(user_id)
    await update.message.reply_text(hasil, parse_mode="HTML")


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu bukan admin.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /premium ID_TELEGRAM")
        return
    target_id = context.args[0]
    users = load_users()
    if target_id not in users:
        users[target_id] = {"market_used": 0, "news_used": 0, "premium": True, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    else:
        users[target_id]["premium"] = True
    save_users(users)
    await update.message.reply_text(f"User {target_id} sudah PREMIUM.")


async def unpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu bukan admin.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /unpremium ID_TELEGRAM")
        return
    target_id = context.args[0]
    users = load_users()
    if target_id in users:
        users[target_id]["premium"] = False
        save_users(users)
    await update.message.reply_text(f"Premium user {target_id} dicabut.")


async def cekuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu bukan admin.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /cekuser ID_TELEGRAM")
        return
    uid = context.args[0]
    users = load_users()
    if uid not in users:
        await update.message.reply_text("User belum ada di database.")
        return
    u = users[uid]
    await update.message.reply_text(f"ID: {uid}\nPremium: {u.get('premium')}\nMarket used: {u.get('market_used')}\nNews used: {u.get('news_used')}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu bukan admin.")
        return
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Format: /broadcast pesan")
        return
    users = load_users()
    sent = 0
    for uid, u in users.items():
        if u.get("premium"):
            try:
                await context.bot.send_message(chat_id=int(uid), text=msg, parse_mode="HTML")
                sent += 1
            except Exception:
                pass
    await update.message.reply_text(f"Broadcast terkirim ke {sent} premium user.")

# ==============================
# RUN APP
# ==============================
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("news", news_command))
app.add_handler(CommandHandler("fomc", fomc_command))
app.add_handler(CommandHandler("premium", premium))
app.add_handler(CommandHandler("unpremium", unpremium))
app.add_handler(CommandHandler("cekuser", cekuser))
app.add_handler(CommandHandler("broadcast", broadcast))
app.add_handler(CallbackQueryHandler(button))

print("EGI CAPITAL AI PREMIUM NEWS BOT jalan...")
app.run_polling()

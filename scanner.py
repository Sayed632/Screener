import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime

def get_secret(key):
    return os.environ.get(key)

TELEGRAM_TOKEN = get_secret('TELEGRAM_TOKEN')
MY_CHAT_ID = get_secret('MY_CHAT_ID')
SCREENER_USERNAME = get_secret('SCREENER_USERNAME')
SCREENER_PASSWORD = get_secret('SCREENER_PASSWORD')

if not all([SCREENER_USERNAME, SCREENER_PASSWORD]):
    print("❌ Critical Screener secrets missing")
    exit(1)

# ========== 10 SCREENS ==========
SCREENER_URLS = {
    "High Piotroski": "https://www.screener.in/screens/4000817/high-piotroski-score/",
    "Magic Formula": "https://www.screener.in/screens/4000836/magic-formula-greenblatt/",
    "Darvas Scan": "https://www.screener.in/screens/4000848/darvas-scan/",
    "Jim Slater Zulu": "https://www.screener.in/screens/4003190/jim-slater-zulu/",
    "Driehaus Momentum": "https://www.screener.in/screens/4004103/richard-driehaus-momentum-screen/",
    "Altman Z Score": "https://www.screener.in/screens/4005994/altman-z-score/",
    "Crash Recovery": "https://www.screener.in/screens/4009908/crash-recovery-screener/",
    "Quality 40-80": "https://www.screener.in/screens/2062976/penny-stocks/",
    "Swing Pick": "https://www.screener.in/screens/4026023/swing-pick/",
    "High FII + Promoter": "https://www.screener.in/screens/4033828/high-fii-promoter/"
}

def get_screener_session():
    session = requests.Session()
    login_url = "https://www.screener.in/login/"
    try:
        init_res = session.get(login_url, timeout=15)
        soup = BeautifulSoup(init_res.text, 'html.parser')
        csrf = soup.find('input', {'name': 'csrfmiddlewaretoken'})
        if not csrf:
            print("❌ CSRF token not found")
            return None
        payload = {
            'username': SCREENER_USERNAME,
            'password': SCREENER_PASSWORD,
            'csrfmiddlewaretoken': csrf['value']
        }
        res = session.post(login_url, data=payload, headers={'Referer': login_url}, timeout=15)
        if "logout" in res.text.lower() or res.status_code == 200:
            print("✅ Screener login successful")
            return session
        print("❌ Screener login failed")
        return None
    except Exception as e:
        print(f"⚠️ Login error: {e}")
        return None

def scan_screens(session):
    all_stocks = {}
    screen_counts = {}

    for name, url in SCREENER_URLS.items():
        print(f"🔍 Scanning: {name}")
        try:
            res = session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, 'html.parser')
            count = 0
            for row in soup.select("table.data-table tbody tr"):
                link = row.select_one("td a")
                if link and '/company/' in link.get('href', ''):
                    stock = link.text.strip()
                    cols = row.find_all('td')
                    price = cols[2].text.strip().replace(',', '') if len(cols) > 2 else "N/A"

                    if stock not in all_stocks:
                        all_stocks[stock] = {
                            "name": stock,
                            "price": price,
                            "url": "https://www.screener.in" + link['href'],
                            "screens": []
                        }
                    if name not in all_stocks[stock]["screens"]:
                        all_stocks[stock]["screens"].append(name)
                    count += 1
            screen_counts[name] = count
            print(f"   → {count} stocks")
        except Exception as e:
            print(f"⚠️ Error on {name}: {e}")
            screen_counts[name] = 0

    print(f"✅ Total unique stocks: {len(all_stocks)}")
    return all_stocks, screen_counts

def get_news(stock_name):
    try:
        query = stock_name.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={query}+stock+OR+shares&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(url)
        positive = ['order', 'win', 'bag', 'contract', 'profit', 'rise', 'surge', 'growth', 'expand', 'beat', 'acquire']
        negative = ['loss', 'fall', 'drop', 'decline', 'fraud', 'probe', 'penalty']
        items = []
        sentiment = "Neutral"
        for entry in feed.entries[:3]:
            title = entry.title
            lower = title.lower()
            if any(w in lower for w in positive):
                sentiment = "Positive"
                items.append(f"🟢 {title[:85]}")
            elif any(w in lower for w in negative):
                sentiment = "Negative"
                items.append(f"🔴 {title[:85]}")
            else:
                items.append(f"⚪ {title[:85]}")
        return sentiment, items[:2]
    except:
        return "Neutral", []

def send_telegram(text):
    if not TELEGRAM_TOKEN or not MY_CHAT_ID:
        return False
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": MY_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            },
            timeout=15
        )
        return res.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def is_high_priority(screens):
    if len(screens) >= 3:
        return True
    if "Crash Recovery" in screens:
        return True
    if "Swing Pick" in screens:
        return True
    if "High FII + Promoter" in screens:
        return True
    if "Quality 40-80" in screens and len(screens) >= 2:
        return True
    return False

def main():
    print("🔄 Starting...")

    heartbeat = (
        "❤️ *Guru Screener Bot*\n\n"
        f"✅ Running at `{datetime.now().strftime('%Y-%m-%d %H:%M')}`\n"
        "📊 10 Screens Active\n"
        "• High Piotroski  • Magic Formula\n"
        "• Darvas  • Zulu  • Driehaus\n"
        "• Altman Z  • Crash Recovery\n"
        "• Quality 40-80  • Swing Pick\n"
        "• High FII + Promoter\n\n"
        "🔔 Telegram: High-priority stocks only\n"
        "📈 Full list → Streamlit Dashboard"
    )
    send_telegram(heartbeat)

    session = get_screener_session()
    if not session:
        send_telegram("❌ Screener login failed. Check secrets.")
        return

    stocks, counts = scan_screens(session)

    if not stocks:
        send_telegram("⚠️ No stocks found today.")
        return

    # Save for Streamlit
    results = {
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_stocks": len(stocks),
        "screen_counts": counts,
        "stocks": []
    }
    for name, data in stocks.items():
        results["stocks"].append({
            "name": name,
            "price": data["price"],
            "screens": ", ".join(data["screens"]),
            "screen_count": len(data["screens"]),
            "url": data["url"]
        })
    results["stocks"] = sorted(results["stocks"], key=lambda x: x["screen_count"], reverse=True)

    with open("scan_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("✅ scan_results.json saved")

    # Summary
    summary = [f"📊 *Scan Summary* — {datetime.now().strftime('%d %b %H:%M')}\n"]
    summary.append(f"Total unique stocks: *{len(stocks)}*\n")
    for k, v in counts.items():
        summary.append(f"• {k}: {v}")
    summary.append("\nSending high-priority stocks...")
    send_telegram("\n".join(summary))

    # Send high-priority stocks
    sorted_stocks = sorted(stocks.items(), key=lambda x: len(x[1]["screens"]), reverse=True)
    sent = 0
    for name, data in sorted_stocks:
        if not is_high_priority(data["screens"]):
            continue
        sent += 1
        if sent > 15:
            break

        screens_str = ", ".join(data["screens"])
        sentiment, news = get_news(name)
        news_text = "\n".join(news) if news else "• No major news"

        tags = []
        if "Crash Recovery" in data["screens"]:
            tags.append("🚀 Recovery")
        if "Swing Pick" in data["screens"]:
            tags.append("📈 Swing")
        if "High FII + Promoter" in data["screens"]:
            tags.append("🏛 High FII + Promoter")
        if "Quality 40-80" in data["screens"]:
            tags.append("💎 40-80")
        if len(data["screens"]) >= 3:
            tags.append("🔥 Multi-Screen")
        if sentiment == "Positive":
            tags.append("📰 News+")
        elif sentiment == "Negative":
            tags.append("⚠️ News-")

        # Special heading
        if "High FII + Promoter" in data["screens"]:
            heading = "🏛 *HIGH FII + PROMOTER*"
        elif "Swing Pick" in data["screens"]:
            heading = "📈 *SWING PICK*"
        else:
            heading = f"📌 *High Priority #{sent}*"

        msg = (
            f"{heading}\n"
            f"📊 *{name}*\n"
            f"💰 ₹{data['price']}\n"
            f"🏷 `{screens_str}`\n"
            f"{' | '.join(tags)}\n"
            f"🔗 [Screener]({data['url']})\n\n"
            f"*News:*\n{news_text}"
        )
        send_telegram(msg)
        print(f"🚀 Sent: {name}")

    final = (
        f"✅ *Scan Done*\n\n"
        f"High-priority stocks sent: {sent}\n"
        f"Total stocks in dashboard: {len(stocks)}"
    )
    send_telegram(final)
    print("✅ Completed")

if __name__ == "__main__":
    main()
import os
import io
import json
import requests
import pandas as pd
import feedparser
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
import re
from datetime import datetime

def get_secret(key):
    val = os.environ.get(key)
    if val:
        return val
    try:
        from google.colab import userdata
        return userdata.get(key)
    except ImportError:
        return None

TELEGRAM_TOKEN = get_secret('TELEGRAM_TOKEN')
MY_CHAT_ID = get_secret('MY_CHAT_ID')
SCREENER_USERNAME = get_secret('SCREENER_USERNAME')
SCREENER_PASSWORD = get_secret('SCREENER_PASSWORD')
DSIJ_USERNAME = get_secret('DSIJ_USERNAME')
DSIJ_PASSWORD = get_secret('DSIJ_PASSWORD')
DISCORD_WEBHOOK_URL = get_secret('DISCORD_WEBHOOK_URL')

if not all([SCREENER_USERNAME, SCREENER_PASSWORD]):
    print("❌ Critical Screener secret resolution failed.")
    exit(1)

SCREENER_URLS = [
    "https://www.screener.in/screens/3708883/small-stocks/",
    "https://www.screener.in/screens/3708803/big-money-inflow/"
]

DSIJ_TARGET_URLS = {
    "Swing Trading": "https://insights.dsij.in/insight/trending-news/swing-trading",
    "Penny Stocks": "https://insights.dsij.in/insight/trending-news/penny-stocks",
    "Multibagger": "https://insights.dsij.in/insight/trending-news/multibagger",
    "Experts Speak": "https://insights.dsij.in/insight/knowledge/experts-speak",
    "Mindshare": "https://insights.dsij.in/insight/trending-news/mindshare",
    "Screener Multibaggers": "https://insights.dsij.in/screener_details/operationtype/multibaggers",
    "Bonus/Split": "https://insights.dsij.in/insight/trending-news/bonus-stock-split"
}

HISTORY_FILE = "holdings_history.json"
CHARTS_DIR = "charts"

if not os.path.exists(CHARTS_DIR):
    os.makedirs(CHARTS_DIR)

if os.path.exists(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > 0:
    with open(HISTORY_FILE, "r") as f:
        try:
            historical_db = json.load(f)
        except json.JSONDecodeError:
            historical_db = {}
else:
    historical_db = {}

def get_screener_session():
    session = requests.Session()
    login_url = "https://www.screener.in/login/"
    try:
        init_res = session.get(login_url, timeout=10)
        soup = BeautifulSoup(init_res.text, 'html.parser')
        csrf_token = soup.find('input', {'name': 'csrfmiddlewaretoken'})
        if not csrf_token:
            return None
        payload = {
            'username': SCREENER_USERNAME,
            'password': SCREENER_PASSWORD,
            'csrfmiddlewaretoken': csrf_token['value']
        }
        res = session.post(login_url, data=payload, headers={'Referer': login_url}, timeout=10)
        return session if "logout" in res.text.lower() or res.status_code == 200 else None
    except Exception as e:
        print(f"⚠️ Screener session error: {e}")
        return None

def get_dsij_session():
    if not DSIJ_USERNAME or not DSIJ_PASSWORD:
        return requests.Session()
    session = requests.Session()
    login_url = "https://www.dsij.in/web/login"
    try:
        session.get(login_url, timeout=10)
        payload = {'login': DSIJ_USERNAME, 'password': DSIJ_PASSWORD}
        session.post(login_url, data=payload, timeout=10)
        return session
    except Exception as e:
        print(f"⚠️ DSIJ Auth exception: {e}")
        return requests.Session()

def scan_screener_urls(session):
    all_stocks = {}
    for url in SCREENER_URLS:
        res = session.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        for row in soup.select("table.data-table tbody tr"):
            link_tag = row.select_one("td a")
            if link_tag and '/company/' in link_tag['href']:
                name = link_tag.text.strip()
                cols = row.find_all('td')
                try:
                    price = cols[2].text.strip().replace(',', '')
                    market_cap = cols[1].text.strip().replace(',', '')
                except:
                    price = "N/A"
                    market_cap = "N/A"
                all_stocks[name] = {
                    'url': f"https://www.screener.in{link_tag['href']}",
                    'price': price,
                    'market_cap': market_cap
                }
    return all_stocks

def fetch_dsij_insights(session):
    dsij_matches = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    for category, url in DSIJ_TARGET_URLS.items():
        try:
            res = session.get(url, headers=headers, timeout=12)
            soup = BeautifulSoup(res.text, 'html.parser')
            text_blocks = soup.find_all(['a', 'h3', 'h4', 'strong', 'p'])
            for block in text_blocks:
                token = block.get_text().strip()
                if len(token) > 2 and len(token) < 40 and not any(x in token.lower() for x in ['login', 'read more', 'click', 'subscribe']):
                    cleaned_name = re.sub(r'\b(Ltd|Limited|Shares|Stock|Incentive)\b\.?', '', token, flags=re.IGNORECASE).strip()
                    if len(cleaned_name) > 2:
                        if cleaned_name not in dsij_matches:
                            dsij_matches[cleaned_name] = []
                        if category not in dsij_matches[cleaned_name]:
                            dsij_matches[cleaned_name].append(category)
        except Exception as e:
            print(f"⚠️ Error parsing DSIJ link [{category}]: {e}")
            continue
    return dsij_matches

def parse_shareholding_metrics(session, company_url):
    res = session.get(company_url, timeout=10)
    soup = BeautifulSoup(res.text, 'html.parser')
    
    order_book_val = "N/A"
    page_text = soup.get_text()
    matches = re.findall(r'(?:order\s*book.*?Rs\.?\s*(\d+,?\d*)\s*Cr|order\s*book.*?(\d+,?\d*)\s*crore)', page_text, re.IGNORECASE)
    if matches:
        order_book_val = f"₹{[''.join(x) for x in matches][0]} Cr"

    mcap_element = soup.select_one("li:-markdown-conjoint(Market Cap) span.number")
    mcap_val = mcap_element.text.strip().replace(',', '') if mcap_element else "N/A"

    section = soup.find(id="shareholding")
    if not section:
        return None
    table = section.find('table', class_='data-table')
    if not table:
        return None
    
    headers = [th.text.strip() for th in table.find('thead').find_all('th')]
    latest_idx = len(headers) - 1
    
    metrics = {
        'Promoters': 0.0, 'FIIs': 0.0, 'DIIs': 0.0, 'Public': 0.0,
        'Top_Holders': [], 'Order_Book': order_book_val, 'Market_Cap_Value': mcap_val
    }
    tbody = table.find('tbody')
    if not tbody:
        return None
    
    for row in tbody.find_all('tr'):
        cols = row.find_all('td')
        if not cols:
            continue
        row_classes = row.get('class', [])
        if 'sub' in row_classes:
            holder_name = cols[0].text.strip()
            try:
                holder_val = cols[latest_idx].text.strip().replace('%', '')
                if holder_val and float(holder_val) > 0.0:
                    metrics['Top_Holders'].append(f"{holder_name} ({holder_val}%)")
            except:
                pass
        else:
            cat = cols[0].text.strip().replace('+', '').strip()
            if cat in metrics:
                try:
                    val = cols[latest_idx].text.strip().replace('%', '')
                    metrics[cat] = float(val) if val else 0.0
                except:
                    pass
                
    return metrics

def fetch_moneycontrol_summaries(company_name):
    news_items = []
    query_url = f"https://news.google.com/rss/search?q={company_name}+site:moneycontrol.com&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        feed = feedparser.parse(query_url)
        for entry in feed.entries[:2]:
            title_clean = entry.title.split('-')[0].strip()
            summary_tag = "⚠️ Corporate Action"
            if any(x in title_clean.lower() for x in ['profit', 'rise', 'surge', 'jump', 'beats']):
                summary_tag = "🚀 Strong Earnings Impact"
            elif any(x in title_clean.lower() for x in ['deal', 'order', 'bags', 'win', 'acquire']):
                summary_tag = "💼 Order Win / Expansion"
            elif any(x in title_clean.lower() for x in ['fall', 'drop', 'slump', 'loss']):
                summary_tag = "📉 Negative Margin Pressure"
                
            news_items.append(f"• *[{summary_tag}]* [{title_clean}]({entry.link})")
    except Exception as e:
        print(f"⚠️ News pull error: {e}")
    return "\n".join(news_items) if news_items else "• _No critical breaking updates detected_"

def get_market_cap_category(mcap_str):
    try:
        mcap = float(mcap_str.replace('₹', '').replace('Cr', '').strip())
        if mcap < 5000:
            return "🔴 SMALL CAP"
        elif 5000 <= mcap < 20000:
            return "🟡 MID CAP"
        else:
            return "🟢 HIGH CAP (LARGE CAP)"
    except:
        return "⚪ CAP CATEGORY UNKNOWN"

def generate_recommendation_rating(metrics, has_changed, dsij_tags):
    score = 0
    if metrics['FIIs'] > 0 or metrics['DIIs'] > 0:
        score += 1
    if dsij_tags:
        score += 2
    if metrics['Order_Book'] != "N/A":
        score += 1

    if score >= 3:
        return "🔥 STRONG BUY (High Conviction Confluence)"
    elif score == 2:
        return "📈 ACCUMULATE / WATCH (Bullish Setup)"
    else:
        return "👀 HOLD / NEUTRAL (Awaiting Inflows)"

def generate_donut_chart(stock_name, metrics):
    labels = ['Promoters', 'FIIs', 'DIIs', 'Public']
    sizes = [metrics.get(k, 0.0) for k in labels]
    filtered_labels = [f"{l}\n({metrics[l]}%)" for l, s in zip(labels, sizes) if s > 0]
    filtered_sizes = [s for s in sizes if s > 0]
    colors = ['#1f77b4', '#2ca02c', '#bcbd22', '#ff7f0e']
    
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(filtered_sizes, labels=filtered_labels, autopct='%1.2f%%', startangle=90,
           colors=colors[:len(filtered_sizes)], pctdistance=0.75)
    centre_circle = plt.Circle((0, 0), 0.50, fc='white')
    fig.gca().add_artist(centre_circle)
    ax.axis('equal')
    plt.title(f"Shareholding Pattern - {stock_name}\n(Latest Quarter Overview)", fontsize=14, fontweight='bold', pad=20)
    plt.tight_layout()
    
    safe_filename = "".join([c for c in stock_name if c.isalpha() or c.isdigit() or c == ' ']).rstrip()
    local_path = os.path.join(CHARTS_DIR, f"{safe_filename.replace(' ', '_')}_shareholding.png")
    plt.savefig(local_path, format='png', dpi=150)
    
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', dpi=150)
    img_buf.seek(0)
    plt.close()
    return img_buf

def calculate_delta_signals(stock_name, current_metrics):
    past = historical_db.get(stock_name, {})
    if not past:
        return "🆕 *Added to Tracking Watchlist*", True
    updates, has_changed = [], False
    for key in ['Promoters', 'FIIs', 'DIIs', 'Public']:
        old_val = past.get(key, 0.0)
        new_val = current_metrics.get(key, 0.0)
        diff = round(new_val - old_val, 2)
        if diff > 0:
            updates.append(f"🟢 {key}: {new_val}% (+{diff}%)")
            has_changed = True
        elif diff < 0:
            updates.append(f"🔴 {key}: {new_val}% ({diff}%)")
            has_changed = True
        else:
            updates.append(f"⚫ {key}: {new_val}% (0.0%)")
    return "\n".join(updates), has_changed

def broadcast_telegram_visual_payload(msg, image_buffer):
    if not TELEGRAM_TOKEN or not MY_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    image_buffer.seek(0)
    files = {'photo': ('shareholding.png', image_buffer.read(), 'image/png')}
    payload = {'chat_id': MY_CHAT_ID, 'caption': msg, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, files=files, timeout=15)
    except Exception as e:
        print(f"Telegram Err: {e}")

def broadcast_discord_payload(msg, image_buffer):
    if not DISCORD_WEBHOOK_URL:
        return
    image_buffer.seek(0)
    payload = {"content": msg}
    files = {"file": ("shareholding.png", image_buffer.read(), "image/png")}
    try:
        requests.post(DISCORD_WEBHOOK_URL, data=payload, files=files, timeout=15)
    except Exception as e:
        print(f"Discord Err: {e}")

def main():
    # ========== TEMPORARY FORCED TEST ==========
    print("🔄 Starting forced Telegram test...")
    
    if not TELEGRAM_TOKEN or not MY_CHAT_ID:
        print("❌ TELEGRAM_TOKEN or MY_CHAT_ID is missing!")
        return
    
    test_msg = (
        "✅ *Screener Bot Test Successful!*\n\n"
        "Your Token and Chat ID are working correctly.\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": MY_CHAT_ID,
                "text": test_msg,
                "parse_mode": "Markdown"
            },
            timeout=15
        )
        print(f"Telegram response: {res.status_code} - {res.text}")
        
        if res.status_code == 200:
            print("✅ Test message sent successfully!")
        else:
            print("❌ Failed to send test message")
    except Exception as e:
        print(f"❌ Telegram Error: {e}")
    
    print("🔄 Forced test completed. Now running normal scan...")
    # ========== END OF FORCED TEST ==========

    screener_session = get_screener_session()
    dsij_session = get_dsij_session()
    if not screener_session:
        print("❌ Screener login failed")
        return

    dsij_insights_db = fetch_dsij_insights(dsij_session)
    active_screener_matches = scan_screener_urls(screener_session)
    
    for name, details in active_screener_matches.items():
        try:
            metrics = parse_shareholding_metrics(screener_session, details['url'])
            if not metrics:
                continue
        except Exception:
            continue
        
        delta_report_string, has_changed = calculate_delta_signals(name, metrics)
        mc_news_summaries = fetch_moneycontrol_summaries(name)
        
        matched_dsij_tags = []
        for raw_dsij_key, tags in dsij_insights_db.items():
            if raw_dsij_key.lower() in name.lower() or name.lower() in raw_dsij_key.lower():
                matched_dsij_tags.extend(tags)
        
        dsij_tag_str = ", ".join(set(matched_dsij_tags)) if matched_dsij_tags else "None detected"
        holders = ", ".join(metrics['Top_Holders'][:2]) if metrics['Top_Holders'] else "None declared"
        
        cap_category = get_market_cap_category(metrics.get('Market_Cap_Value', '0'))
        recommendation = generate_recommendation_rating(metrics, has_changed, list(set(matched_dsij_tags)))
        
        tele_msg = (
            f"📌 *CATEGORY: {cap_category}*\n"
            f"📊 *Stock Report: {name}*\n"
            f"💰 Price: ₹{details['price']} | 📦 Order Book: {metrics['Order_Book']}\n"
            f"📢 *RECOMMENDATION:* `{recommendation}`\n"
            f"🎯 *DSIJ Trends:* `{dsij_tag_str}`\n"
            f"👥 *Investors:* {holders}\n"
            f"🔗 [Screener Profile]({details['url']})\n\n"
            f"*Shareholding Activity:*\n{delta_report_string}\n\n"
            f"*Summarized News Inputs:*\n{mc_news_summaries}"
        )
        
        if len(tele_msg) > 1024:
            tele_msg = tele_msg[:1010] + "..."

        if has_changed or matched_dsij_tags or name not in historical_db:
            chart_img = generate_donut_chart(name, metrics)
            broadcast_telegram_visual_payload(tele_msg, chart_img)
            broadcast_discord_payload(tele_msg, chart_img)
            print(f"🚀 Dispatched filtered cap report for {name}")
            
        historical_db[name] = metrics

    with open(HISTORY_FILE, "w") as f:
        json.dump(historical_db, f, indent=4)

if __name__ == "__main__":
    main()
import os
import io
import json
import requests
import pandas as pd
import feedparser
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
import re

# Smart Environment Resolver
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

if not all([TELEGRAM_TOKEN, MY_CHAT_ID, SCREENER_USERNAME, SCREENER_PASSWORD]):
    print("❌ Critical Screener/Telegram secret resolution failed.")
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
        if not csrf_token: return None
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
    """Authenticates session against DSIJ core login framework handles underlying Odoo cookie tracking"""
    if not DSIJ_USERNAME or not DSIJ_PASSWORD:
        print("ℹ️ DSIJ Credentials missing. Processing public insights only.")
        return requests.Session()
        
    session = requests.Session()
    login_url = "https://www.dsij.in/web/login"
    try:
        init_res = session.get(login_url, timeout=10)
        soup = BeautifulSoup(init_res.text, 'html.parser')
        csrf_token = soup.find('input', {'name': 'csrf_token'})
        
        payload = {
            'login': DSIJ_USERNAME,
            'password': DSIJ_PASSWORD,
        }
        if csrf_token:
            payload['csrf_token'] = csrf_token['value']
            
        session.post(login_url, data=payload, timeout=10)
        return session
    except Exception as e:
        print(f"⚠️ DSIJ Auth exception: {e}. Falling back to unauthenticated public requests.")
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
                except:
                    price = "N/A"
                all_stocks[name] = {
                    'url': f"https://www.screener.in{link_tag['href']}",
                    'price': price
                }
    return all_stocks

def fetch_dsij_insights(session):
    """Scans requested insight portals and maps mentioned companies to categories"""
    dsij_matches = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    for category, url in DSIJ_TARGET_URLS.items():
        try:
            res = session.get(url, headers=headers, timeout=12)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # Extracts anchor tracking links, headers, and bold text context elements
            text_blocks = soup.find_all(['a', 'h3', 'h4', 'strong', 'p'])
            for block in text_blocks:
                token = block.get_text().strip()
                # Simple regex cleaning identifying potential Indian Equity Symbol/Name references
                if len(token) > 2 and len(token) < 40 and not any(x in token.lower() for x in ['login', 'read more', 'click', 'subscribe']):
                    # Clean up common junk noise trailing text elements
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

    section = soup.find(id="shareholding")
    if not section: return None
    table = section.find('table', class_='data-table')
    if not table: return None
    
    headers = [th.text.strip() for th in table.find('thead').find_all('th')]
    latest_idx = len(headers) - 1
    
    metrics = {'Promoters': 0.0, 'FIIs': 0.0, 'DIIs': 0.0, 'Public': 0.0, 'Top_Holders': [], 'Order_Book': order_book_val}
    tbody = table.find('tbody')
    if not tbody: return None
    
    for row in tbody.find_all('tr'):
        cols = row.find_all('td')
        if not cols: continue
        row_classes = row.get('class', [])
        if 'sub' in row_classes:
            holder_name = cols[0].text.strip()
            try:
                holder_val = cols[latest_idx].text.strip().replace('%', '')
                if holder_val and float(holder_val) > 0.0:
                    metrics['Top_Holders'].append(f"{holder_name} ({holder_val}%)")
            except:
                continue
        else:
            cat = cols[0].text.strip().replace('+', '').strip()
            if cat in metrics:
                try:
                    val = cols[latest_idx].text.strip().replace('%', '')
                    metrics[cat] = float(val) if val else 0.0
                except:
                    continue
                
    return metrics

def fetch_moneycontrol_news(company_name):
    news_items = []
    query_url = f"https://news.google.com/rss/search?q={company_name}+site:moneycontrol.com&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        feed = feedparser.parse(query_url)
        for entry in feed.entries[:2]:
            title_clean = entry.title.split('-')[0].strip()
            news_items.append(f"• [{title_clean}]({entry.link})")
    except Exception as e:
        print(f"⚠️ Error pulling news updates for {company_name}: {e}")
    return "\n".join(news_items) if news_items else "• _No recent structural Moneycontrol updates indices detected_"

def generate_donut_chart(stock_name, metrics):
    labels = ['Promoters', 'FIIs', 'DIIs', 'Public']
    sizes = [metrics.get(k, 0.0) for k in labels]
    filtered_labels = [f"{l}\n({metrics[l]}%)" for l, s in zip(labels, sizes) if s > 0]
    filtered_sizes = [s for s in sizes if s > 0]
    colors = ['#1f77b4', '#2ca02c', '#bcbd22', '#ff7f0e']
    
    fig, ax = plt.subplots(figsize=(6, 6))
    wedges, texts, autotexts = ax.pie(
        filtered_sizes, labels=filtered_labels, autopct='%1.2f%%',
        startangle=90, colors=colors[:len(filtered_sizes)], pctdistance=0.75,
        textprops=dict(color="black", size=11)
    )
    centre_circle = plt.Circle((0,0), 0.50, fc='white')
    fig.gca().add_artist(centre_circle)
    ax.axis('equal')  
    plt.title(f"Shareholding Pattern - {stock_name}\n(Latest Quarter Overview)", fontsize=14, fontweight='bold', pad=20)
    plt.tight_layout()
    
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', dpi=150)
    img_buf.seek(0)
    plt.close()
    return img_buf

def calculate_delta_signals(stock_name, current_metrics):
    past = historical_db.get(stock_name, {})
    if not past:
        return "🆕 *Added to Tracking Watchlist*", True
    updates = []
    has_changed = False
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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {'photo': ('shareholding.png', image_buffer, 'image/png')}
    payload = {'chat_id': MY_CHAT_ID, 'caption': msg, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, files=files, timeout=15)
    except Exception as e:
        print(f"⚠️ Telegram broadcast exception: {e}")

def main():
    print("🔐 Starting dual login validation sequence...")
    screener_session = get_screener_session()
    dsij_session = get_dsij_session()
    
    if not screener_session:
        print("❌ Screener initialization failed."); return

    print("📡 Extracting active DSIJ portal trend matrices...")
    dsij_insights_db = fetch_dsij_insights(dsij_session)
    
    active_screener_matches = scan_screener_urls(screener_session)
    print(f"📈 Found {len(active_screener_matches)} baseline target stock matches.")
    
    for name, details in active_screener_matches.items():
        try:
            metrics = parse_shareholding_metrics(screener_session, details['url'])
            if not metrics: continue
        except Exception as e:
            print(f"⚠️ Error parsing shareholding for {name}: {e}. Skipping...")
            continue
        
        delta_report_string, has_changed = calculate_delta_signals(name, metrics)
        mc_news = fetch_moneycontrol_news(name)
        
        # Cross-reference logic checking if stock is trending inside DSIJ insights keywords
        matched_dsij_tags = []
        for raw_dsij_key, tags in dsij_insights_db.items():
            if raw_dsij_key.lower() in name.lower() or name.lower() in raw_dsij_key.lower():
                matched_dsij_tags.extend(tags)
        
        dsij_tag_str = ", ".join(set(matched_dsij_tags)) if matched_dsij_tags else "None detected"
        holders = ", ".join(metrics['Top_Holders'][:2]) if metrics['Top_Holders'] else "None declared"
        
        tele_msg = (
            f"📊 *Stock Report: {name}*\n"
            f"💰 Price: ₹{details['price']} | 📦 Order Book: {metrics['Order_Book']}\n"
            f"🎯 *DSIJ Trends:* `{dsij_tag_str}`\n"
            f"👥 *Investors:* {holders}\n"
            f"🔗 [Screener Profile]({details['url']})\n\n"
            f"*Shareholding Activity:*\n{delta_report_string}\n\n"
            f"*Moneycontrol News:*\n{mc_news}"
        )
        
        if len(tele_msg) > 1024:
            tele_msg = tele_msg[:1010] + "..."

        # Trigger update if metrics shifted OR if the stock crosses over into a DSIJ watchlist category
        if has_changed or matched_dsij_tags or name not in historical_db:
            chart_img = generate_donut_chart(name, metrics)
            broadcast_telegram_visual_payload(tele_msg, chart_img)
            print(f"🚀 Telegram image alert deployed for {name} [DSIJ Cross-Match: {bool(matched_dsij_tags)}]")
            
        historical_db[name] = metrics

    with open(HISTORY_FILE, "w") as f:
        json.dump(historical_db, f, indent=4)
    print("💾 Sync operations complete.")

if __name__ == "__main__":
    main()

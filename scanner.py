import os
import io
import json
import requests
import pandas as pd
import feedparser
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup

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

if not all([TELEGRAM_TOKEN, MY_CHAT_ID, SCREENER_USERNAME, SCREENER_PASSWORD]):
    print("❌ Secret resolution failed.")
    exit(1)

SCREENER_URLS = [
    "https://www.screener.in/screens/3708883/small-stocks/",
    "https://www.screener.in/screens/3708803/big-money-inflow/"
]

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
    init_res = session.get(login_url)
    soup = BeautifulSoup(init_res.text, 'html.parser')
    csrf_token = soup.find('input', {'name': 'csrfmiddlewaretoken'})
    
    if not csrf_token:
        return None
        
    payload = {
        'username': SCREENER_USERNAME,
        'password': SCREENER_PASSWORD,
        'csrfmiddlewaretoken': csrf_token['value']
    }
    
    res = session.post(login_url, data=payload, headers={'Referer': login_url})
    return session if "logout" in res.text.lower() or res.status_code == 200 else None

def scan_screener_urls(session):
    all_stocks = {}
    for url in SCREENER_URLS:
        res = session.get(url)
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

def parse_shareholding_metrics(session, company_url):
    res = session.get(company_url)
    soup = BeautifulSoup(res.text, 'html.parser')
    
    # 1. Parse Order Book references if available within company page boxes
    order_book_val = "N/A"
    page_text = soup.get_text()
    import re
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

def fetch_external_news(company_name):
    """Gathers recent news items via aggregated open structures."""
    news_items = []
    # Query Google News RSS wrapper looking for explicit financial news nodes
    query_url = f"https://news.google.com/rss/search?q={company_name}+(site:moneycontrol.com+OR+site:dsij.in)&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        feed = feedparser.parse(query_url)
        for entry in feed.entries[:3]: # Grab latest 3 context links
            news_items.append(f"• [{entry.title}]({entry.link})")
    except Exception as e:
        print(f"⚠️ Error pulling news updates for {company_name}: {e}")
    
    return "\n".join(news_items) if news_items else "• _No recent Moneycontrol/DSIJ indices detected_"

def generate_donut_chart(stock_name, metrics):
    """Generates an clean, professional donut chart mimicking figure 1000714002.jpg"""
    labels = ['Promoters', 'FIIs', 'DIIs', 'Public']
    sizes = [metrics.get(k, 0.0) for k in labels]
    
    # Filter out absolute zero positions safely to guarantee chart plotting clarity
    filtered_labels = [f"{l}\n({metrics[l]}%)" for l, s in zip(labels, sizes) if s > 0]
    filtered_sizes = [s for s in sizes if s > 0]
    
    colors = ['#1f77b4', '#2ca02c', '#bcbd22', '#ff7f0e'] # Matching color hex schemas
    
    fig, ax = plt.subplots(figsize=(6, 6))
    wedges, texts, autotexts = ax.pie(
        filtered_sizes, 
        labels=filtered_labels, 
        autopct='%1.2f%%', 
        startangle=90, 
        colors=colors[:len(filtered_sizes)],
        pctdistance=0.75,
        textprops=dict(color="black", size=11)
    )
    
    # Transform pie chart to a sleek donut
    centre_circle = plt.Circle((0,0), 0.50, fc='white')
    fig.gca().add_artist(centre_circle)
    
    # Format internal percentage fonts inside the chart
    for autotext in autotexts:
        autotext.set_size(10)
        
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
    """Sends the visualization plot with markdown textual annotations beneath it."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {'photo': ('shareholding.png', image_buffer, 'image/png')}
    payload = {'chat_id': MY_CHAT_ID, 'caption': msg, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, files=files, timeout=15)
    except Exception as e:
        print(f"⚠️ Telegram visual pipeline encountered an execution error: {e}")

def main():
    print("🔐 Starting secure Screener login sequence...")
    session = get_screener_session()
    if not session:
        print("❌ Login authentication rejected."); return
        
    active_screener_matches = scan_screener_urls(session)
    print(f"📈 Found {len(active_screener_matches)} cumulative target stock matches.")
    
    for name, details in active_screener_matches.items():
        try:
            metrics = parse_shareholding_metrics(session, details['url'])
            if not metrics: continue
        except Exception as e:
            print(f"⚠️ Error parsing shareholding for {name}: {e}. Skipping...")
            continue
        
        delta_report_string, has_changed = calculate_delta_signals(name, metrics)
        recent_news = fetch_external_news(name)
        
        # Format Top Institutional Major Holders inside the caption text cleanly
        holders = ", ".join(metrics['Top_Holders'][:3]) if metrics['Top_Holders'] else "None declared"
        
        tele_msg = (
            f"📊 *Stock Report: {name}*\n"
            f"💰 Price: ₹{details['price']} | 📦 Order Book: {metrics['Order_Book']}\n"
            f"👥 *Major Investors:* {holders}\n"
            f"🔗 [Screener Profile]({details['url']})\n\n"
            f"*Shareholding Shifts:*\n{delta_report_string}\n\n"
            f"*Recent News (Moneycontrol/DSIJ):*\n{recent_news}"
        )
        
        # Truncate text string if it hits safety thresholds for standard Telegram captions
        if len(tele_msg) > 1024:
            tele_msg = tele_msg[:1010] + "..."

        if has_changed or name not in historical_db:
            chart_img = generate_donut_chart(name, metrics)
            broadcast_telegram_visual_payload(tele_msg, chart_img)
            print(f"🚀 Telegram image alert sent for {name}")
            
        historical_db[name] = metrics

    with open(HISTORY_FILE, "w") as f:
        json.dump(historical_db, f, indent=4)
    print("💾 Analysis database sync complete.")

if __name__ == "__main__":
    main()

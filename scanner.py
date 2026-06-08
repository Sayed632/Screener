import os
import io
import json
import requests
import pandas as pd
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
    section = soup.find(id="shareholding")
    if not section: return None
    table = section.find('table', class_='data-table')
    if not table: return None
    
    headers = [th.text.strip() for th in table.find('thead').find_all('th')]
    latest_idx = len(headers) - 1
    
    metrics = {'Promoters': 0.0, 'FIIs': 0.0, 'DIIs': 0.0, 'Public': 0.0, 'Top_Holders': []}
    
    # 1. Grab Main Categories
    for row in table.find('tbody', class_='').find_all('tr', class_=''):
        cols = row.find_all('td')
        if not cols: continue
        cat = cols[0].text.strip().replace('+', '').strip()
        if cat in metrics:
            try:
                val = cols[latest_idx].text.strip().replace('%', '')
                metrics[cat] = float(val) if val else 0.0
            except:
                continue
                
    # 2. Extract Hidden Institutional Sub-Holders (Deep Scan)
    for sub_row in table.select("tbody tr.sub"):
        cols = sub_row.find_all('td')
        if len(cols) >= latest_idx + 1:
            holder_name = cols[0].text.strip()
            try:
                holder_val = cols[latest_idx].text.strip().replace('%', '')
                if holder_val and float(holder_val) > 0.0:
                    metrics['Top_Holders'].append(f"{holder_name} ({holder_val}%)")
            except:
                continue
                
    return metrics

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

def broadcast_telegram_payload(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': MY_CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'}
    requests.post(url, data=payload)

def main():
    print("🔐 Starting secure Screener login sequence...")
    session = get_screener_session()
    if not session:
        print("❌ Login authentication rejected."); return
        
    active_screener_matches = scan_screener_urls(session)
    print(f"📈 Found {len(active_screener_matches)} cumulative target stock matches.")
    
    updated_history_snapshot = {}
    
    for name, details in active_screener_matches.items():
        metrics = parse_shareholding_metrics(session, details['url'])
        if not metrics: continue
        
        delta_report_string, has_changed = calculate_delta_signals(name, metrics)
        
        # Format Top Holders list safely
        holders_list = "\n".join([f"  • _{h}_" for h in metrics['Top_Holders']]) if metrics['Top_Holders'] else "  _No institutional major holders declared_"
        
        # Structural Layout
        tele_msg = (
            f"📊 *Stock Report: {name}*\n"
            f"💰 Price: ₹{details['price']}\n"
            f"🔗 [Screener Profile]({details['url']})\n\n"
            f"*Shareholding Activity Summary:*\n"
            f"{delta_report_string}\n\n"
            f"*Identified Institutional Investors:*\n"
            f"{holders_list}"
        )
        
        # Send on brand new additions OR when an institutional shift triggers
        if has_changed or name not in historical_db:
            broadcast_telegram_payload(tele_msg)
            print(f"🚀 Telegram update sent for {name}")
            
        updated_history_snapshot[name] = metrics

    with open(HISTORY_FILE, "w") as f:
        json.dump(updated_history_snapshot, f, indent=4)
    print("💾 Analysis database sync complete.")

if __name__ == "__main__":
    main()

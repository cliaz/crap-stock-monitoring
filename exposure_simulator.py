#! /usr/bin/env python3
# Exposure-scaling simulator.
#
# Strategy: each day, target an equity exposure derived linearly from NYMO
# (the McClellan Oscillator, of which NYSI is the running sum):
#     NYMO = -50  -> 100% invested
#     NYMO =   0  ->  50% invested
#     NYMO = +50  ->   0% invested
# NYMO is clamped to [-50, +50] (it rarely sits outside).
#
# The HTML page lets you compare this against:
#   - the binary NYSI Red/Black strategy (from trading_simulator.py), and
#   - buy-and-hold
# all on the same time window + price data.
#
# UI controls (live, no script re-run needed):
#   - Rebalance threshold slider (% drift before rebalancing)
#   - Fractional vs whole-share checkbox

import math
import requests as rq
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import argparse


DEFAULT_NYMO_TICKER = 'NYMO'
DEFAULT_NYSI_TICKER = 'NYSI'
DEFAULT_STOCK_TICKER = 'GGUS.AX'
DEFAULT_STOCK_MONTHS = 12
DEFAULT_STOCK_PRICE_TYPE = 'Close'

parser = argparse.ArgumentParser(description='Generate exposure-scaling trading simulation webpage.', add_help=False)
parser.add_argument('-s', '--stock', type=str, default=DEFAULT_STOCK_TICKER, help=f'Stock ticker (default: {DEFAULT_STOCK_TICKER})')
parser.add_argument('-m', '--months', type=int, default=DEFAULT_STOCK_MONTHS, help=f'Duration in months (default: {DEFAULT_STOCK_MONTHS})')
parser.add_argument('-p', '--price-type', type=str, default=DEFAULT_STOCK_PRICE_TYPE, help=f'Stock price type (default: {DEFAULT_STOCK_PRICE_TYPE})')
parser.add_argument('-o', '--output', type=str, default=None, help='Output HTML file name')
parser.add_argument('--csv', action='store_true', help='Output as CSV file instead of HTML webpage')
parser.add_argument('--blacklist-start', type=str, default=None, help='Start date for blacklist period (YYYY-MM-DD)')
parser.add_argument('--blacklist-end', type=str, default=None, help='End date for blacklist period (YYYY-MM-DD)')
parser.add_argument('-h', '--help', action='store_true', help='Show help and exit')
args = parser.parse_args()

if args.help:
    print('''\nExposure-scaling Trading Simulation

Usage: python exposure_simulator.py [options]

Options:
  -s, --stock          Stock ticker (default: GGUS.AX)
  -m, --months         Duration in months (default: 12)
  -p, --price-type     Stock price column to use (default: Close)
  -o, --output         Output filename
  --csv                Emit CSV instead of HTML
  --blacklist-start    Start of no-trade window (YYYY-MM-DD)
  --blacklist-end      End of no-trade window (YYYY-MM-DD)
  -h, --help           Show this help

Strategy:
  Each day, target equity exposure from NYMO (clamped to [-50, +50]):
    -50 -> 100% in    |   0 -> 50% in   |   +50 -> 0% in   (linear)
  HTML page exposes a rebalance-threshold slider and fractional-share toggle.
  Compares against binary NYSI Red/Black strategy and buy-and-hold.
''')
    exit(0)

STOCK_TICKER = args.stock
STOCK_MONTHS = args.months
STOCK_PRICE_TYPE = args.price_type

BLACKLIST_START = None
BLACKLIST_END = None
if args.blacklist_start and args.blacklist_end:
    try:
        BLACKLIST_START = datetime.strptime(args.blacklist_start, '%Y-%m-%d').date()
        BLACKLIST_END = datetime.strptime(args.blacklist_end, '%Y-%m-%d').date()
        if BLACKLIST_START > BLACKLIST_END:
            print(f"Warning: blacklist start {BLACKLIST_START} after end {BLACKLIST_END}; ignoring.")
            BLACKLIST_START = None
            BLACKLIST_END = None
        else:
            print(f"Blacklist: {BLACKLIST_START} to {BLACKLIST_END} (inclusive)")
    except ValueError as e:
        print(f"Error parsing blacklist dates: {e}. Use YYYY-MM-DD.")
elif args.blacklist_start or args.blacklist_end:
    print("Warning: both --blacklist-start and --blacklist-end are required.")


# --- Fetchers (shared shape with trading_simulator.py) ---

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'

def build_stockcharts_url(ticker, months):
    years = months // 12
    mn = months % 12
    return f'https://stockcharts.com/c-sc/sc?s=${ticker}&p=D&yr={years}&mn={mn}&dy=0&i=t3757734781c&img=text&inspector=yes'

def months_to_yf_period(months):
    return f"{months // 12}y" if months % 12 == 0 else f"{months}mo"

def fetch_stockcharts_indicator(ticker, months):
    """Fetch a StockCharts daily indicator series. Returns list of {date, value}."""
    url = build_stockcharts_url(ticker, months)
    response = rq.get(url, headers={'user-agent': USER_AGENT})
    text = response.text
    if '<pricedata>' not in text:
        print(f"Error: StockCharts response for ${ticker} missing <pricedata>.")
        print(f"URL: {url}")
        print(f"HTTP status: {response.status_code}")
        print(f"Response snippet: {text[:500]}")
        exit(1)
    body = text.split('<pricedata>')[1].replace('</pricedata>', '')
    out = []
    for line in body.split('|'):
        cols = line.split(' ')
        if len(cols) < 4:
            continue
        date = datetime(int(cols[1][0:4]), int(cols[1][4:6]), int(cols[1][6:8]))
        value = float(cols[3])
        if not math.isnan(value):
            out.append({'date': date, 'value': value})
    return out


# --- Fetch all three series ---

print(f"Fetching ${DEFAULT_NYMO_TICKER}...")
nymo_list = fetch_stockcharts_indicator(DEFAULT_NYMO_TICKER, STOCK_MONTHS)
print(f"Fetching ${DEFAULT_NYSI_TICKER}...")
nysi_list = fetch_stockcharts_indicator(DEFAULT_NYSI_TICKER, STOCK_MONTHS)
print(f"Fetching {STOCK_TICKER} from Yahoo Finance...")
stock_yf = yf.Ticker(STOCK_TICKER)
stock_hist = stock_yf.history(period=months_to_yf_period(STOCK_MONTHS)).reset_index()

if STOCK_PRICE_TYPE not in stock_hist.columns:
    print(f"Error: '{STOCK_PRICE_TYPE}' not in Yahoo data for {STOCK_TICKER}. Available: {list(stock_hist.columns)}")
    exit(1)

stock_price_dict = {}
for _, row in stock_hist.iterrows():
    d = row['Date']
    if isinstance(d, pd.Timestamp):
        d = d.date()
    stock_price_dict[d] = row[STOCK_PRICE_TYPE]


# --- Merge ---

# Inner-join NYMO and NYSI on date (both come from same calendar; should align row-for-row)
nymo_by_date = {r['date']: r['value'] for r in nymo_list}
nysi_by_date = {r['date']: r['value'] for r in nysi_list}
shared_dates = sorted(set(nymo_by_date.keys()) & set(nysi_by_date.keys()))
print(f"NYMO rows: {len(nymo_list)}, NYSI rows: {len(nysi_list)}, shared dates: {len(shared_dates)}")

# Red/Black from NYSI direction (carrying last directional state through flat patches)
nysi_values = [nysi_by_date[d] for d in shared_dates]
red_black = ['n/a']
last_known = 'n/a'
for i in range(1, len(nysi_values)):
    if nysi_values[i] > nysi_values[i-1]:
        last_known = 'Black'
        red_black.append('Black')
    elif nysi_values[i] < nysi_values[i-1]:
        last_known = 'Red'
        red_black.append('Red')
    else:
        red_black.append(last_known)

def get_next_trading_day_price(nysi_date, max_days_ahead=7):
    for days_ahead in range(1, max_days_ahead + 1):
        check_date = (nysi_date + timedelta(days=days_ahead)).date()
        price = stock_price_dict.get(check_date)
        if price is not None:
            return check_date, price
    return None, None

rows = []
for i, d in enumerate(shared_dates):
    trade_date, price = get_next_trading_day_price(d)
    blacklisted = (BLACKLIST_START is not None
                   and trade_date is not None
                   and BLACKLIST_START <= trade_date <= BLACKLIST_END)
    rows.append({
        'date': d,
        'NYMO value': nymo_by_date[d],
        'NYSI value': nysi_by_date[d],
        'Red / Black': red_black[i],
        'Trade Date': trade_date,
        f'Stock Price ({STOCK_PRICE_TYPE})': price,
        'Blacklisted': blacklisted,
    })

df = pd.DataFrame(rows)


# --- Output ---

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
stock_clean = STOCK_TICKER.replace('.', '_')

if args.csv:
    path = args.output or f'NYMO_{stock_clean}_{STOCK_MONTHS}_exposure_sim_{timestamp}.csv'
    df.to_csv(path, index=False)
    print(f'CSV file created: {path}')
else:
    template_path = 'template_exposure_simulation_visualisation.html'
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()

    import re
    csv_string = df.to_csv(index=False)
    csv_pattern = r'(const csvData = `)[\s\S]*?(`;)'
    new_html = re.sub(csv_pattern, lambda m: m.group(1) + csv_string + m.group(2), html)
    new_html = new_html.replace('{{STOCK_TICKER}}', STOCK_TICKER)
    new_html = new_html.replace('{{STOCK_PRICE_TYPE}}', STOCK_PRICE_TYPE)
    new_html = new_html.replace('{{STOCK_MONTHS}}', str(STOCK_MONTHS))

    if BLACKLIST_START and BLACKLIST_END:
        new_html = new_html.replace('{{BLACKLIST_START}}', str(BLACKLIST_START))
        new_html = new_html.replace('{{BLACKLIST_END}}', str(BLACKLIST_END))
        new_html = new_html.replace('{{HAS_BLACKLIST}}', 'true')
    else:
        new_html = new_html.replace('{{BLACKLIST_START}}', '')
        new_html = new_html.replace('{{BLACKLIST_END}}', '')
        new_html = new_html.replace('{{HAS_BLACKLIST}}', 'false')

    path = args.output or f'NYMO_{stock_clean}_{STOCK_MONTHS}_exposure_sim_{timestamp}.html'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print(f'HTML file created: {path}')

#! /usr/bin/env python3
# inspiration from https://wire.insiderfinance.io/how-to-get-the-nyse-mcclellan-summation-index-nysi-in-python-a12a221fde33

# Fetches NYSI data from StockCharts and GGUS.AX data from Yahoo Finance, merges by date, adds trend column
# outputs into a webpage that shows results of trading



import math
import requests as rq
import pandas as pd
import yfinance as yf
from datetime import datetime
import argparse


# Default configuration
DEFAULT_INDICATOR_TICKER = '$NYSI'
DEFAULT_STOCK_TICKER = 'GGUS.AX'
DEFAULT_BUY_SIGNAL = 'Black'        # aka when the NYSI changes to black from red, we buy
DEFAULT_STOCK_MONTHS = 12
DEFAULT_STOCK_PRICE_TYPE = 'Close'

# Argument parsing
parser = argparse.ArgumentParser(description='Generate trading simulation webpage with NYSI and stock data.', add_help=False)
parser.add_argument('-i', '--indicator', type=str, default=DEFAULT_INDICATOR_TICKER, help='Indicator ticker (default: $NYSI)')
parser.add_argument('-s', '--stock', type=str, default=DEFAULT_STOCK_TICKER, help='Stock ticker (default: GGUS.AX)')
parser.add_argument('-b', '--buy-signal', type=str, default=DEFAULT_BUY_SIGNAL, help='Buy signal, aka which colour the NYSI changes to (default: Black)')
parser.add_argument('-m', '--months', type=int, default=DEFAULT_STOCK_MONTHS, help='Duration in months (default: 36)')
parser.add_argument('-p', '--price-type', type=str, default=DEFAULT_STOCK_PRICE_TYPE, help='Stock price type (default: Close)')
parser.add_argument('-o', '--output', type=str, default=None, help='Output HTML file name')
parser.add_argument('--csv', action='store_true', help='Output as CSV file instead of HTML webpage')
parser.add_argument('--blacklist-start', type=str, default=None, help='Start date for blacklist period (YYYY-MM-DD format)')
parser.add_argument('--blacklist-end', type=str, default=None, help='End date for blacklist period (YYYY-MM-DD format)')
parser.add_argument('-h', '--help', action='store_true', help='Show help/README and exit')
args, unknown = parser.parse_known_args()

if args.help:
    help_formatter = argparse.RawDescriptionHelpFormatter(prog='trading_simulator.py')
    help_text = '''\nTrading Simulation Webpage Generator

Usage: python trading_simulator.py [options]

Options:
  -i, --indicator      Indicator ticker (default: $NYSI)
  -s, --stock          Stock ticker (default: GGUS.AX)
  -b, --buy-signal     Buy signal (default: Black)
  -m, --months         Duration in months (default: 36)
  -p, --price-type     Stock price type (default: Close)
  -o, --output         Output HTML file name
  --csv                Output as CSV file instead of HTML webpage
  --blacklist-start    Start date for blacklist period (YYYY-MM-DD format)
  --blacklist-end      End date for blacklist period (YYYY-MM-DD format)
  -h, --help           Show this help message

Description:
  This script fetches NYSI data from StockCharts and stock data from Yahoo Finance,
  merges by date, adds a trend column, and outputs a webpage showing trading results.
  
  The NYSI date alignment logic: NYSI date + 1 day = stock trading date
  This reflects that NYSI values are released after US market close and used for next day's trading.

  Blacklist dates: No trades will be executed between blacklist-start and blacklist-end (inclusive).
  This is useful for avoiding trades during volatile periods, holidays, or other specific timeframes.

Example:
  python trading_simulator.py -i $NYSI -s GGUS.AX -m 12 -p Close
  python trading_simulator.py --csv -o data.csv -m 24
  python trading_simulator.py -m 12 --blacklist-start 2024-12-20 --blacklist-end 2025-01-10

Output:
  HTML: INDICATOR_STOCK_MONTHS_trading_sim_YYYYMMDD_HHMMSS.html (default)
  CSV:  INDICATOR_STOCK_MONTHS_trading_sim_YYYYMMDD_HHMMSS.csv (with --csv)
  Use -o to override the filename.
'''
    print(help_text)
    exit(0)

INDICATOR_TICKER = args.indicator
STOCK_TICKER = args.stock
BUY_SIGNAL = args.buy_signal
STOCK_MONTHS = args.months
STOCK_PRICE_TYPE = args.price_type

# Parse blacklist dates
BLACKLIST_START = None
BLACKLIST_END = None
if args.blacklist_start and args.blacklist_end:
    try:
        BLACKLIST_START = datetime.strptime(args.blacklist_start, '%Y-%m-%d').date()
        BLACKLIST_END = datetime.strptime(args.blacklist_end, '%Y-%m-%d').date()
        if BLACKLIST_START > BLACKLIST_END:
            print(f"Warning: Blacklist start date ({BLACKLIST_START}) is after end date ({BLACKLIST_END}). No blacklist will be applied.")
            BLACKLIST_START = None
            BLACKLIST_END = None
        else:
            print(f"Blacklist period: {BLACKLIST_START} to {BLACKLIST_END} (inclusive)")
    except ValueError as e:
        print(f"Error parsing blacklist dates: {e}. Use YYYY-MM-DD format. No blacklist will be applied.")
        BLACKLIST_START = None
        BLACKLIST_END = None
elif args.blacklist_start or args.blacklist_end:
    print("Warning: Both --blacklist-start and --blacklist-end must be provided. No blacklist will be applied.")


## Get NYSI historical data from stockcharts.com

# Helper to build NYSI URL from months
def build_nysi_url(ticker, months):
    years = months // 12
    mn = months % 12
    return f'https://stockcharts.com/c-sc/sc?s={ticker}&p=D&yr={years}&mn={mn}&dy=0&i=t3757734781c&img=text&inspector=yes'

# Helper to build Yahoo Finance period string from months
def months_to_yf_period(months):
    if months % 12 == 0:
        return f"{months // 12}y"
    else:
        return f"{months}mo"


# Constants
USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
NYSI_URL = build_nysi_url(INDICATOR_TICKER, STOCK_MONTHS)


# Get NYSI raw information
nysi_data = rq.get(NYSI_URL, headers={'user-agent':USER_AGENT}).text
nysi_data = nysi_data.split('<pricedata>')[1].replace('</pricedata>','')

# Process NYSI rows
nysi_lines = nysi_data.split('|')
nysi_list = []
for line in nysi_lines:
    cols = line.split(' ')
    if len(cols) < 4:
        continue
    date = datetime(int(cols[1][0:4]), int(cols[1][4:6]), int(cols[1][6:8]))
    value = float(cols[3])
    if not math.isnan(value):
        nysi_list.append({'date': date, 'NYSI value': value})



## Get stock historical data from Yahoo Finance


# Get stock historical data from Yahoo Finance
stock_yf = yf.Ticker(STOCK_TICKER)
stock_hist = stock_yf.history(period=months_to_yf_period(STOCK_MONTHS))
stock_hist = stock_hist.reset_index()


# Build a dict of date to selected price type
stock_price_dict = {}
for _, row in stock_hist.iterrows():
    date = row['Date']
    # Convert to datetime.date for matching
    if isinstance(date, pd.Timestamp):
        date = date.date()
    price = row[STOCK_PRICE_TYPE] if STOCK_PRICE_TYPE in row else None
    stock_price_dict[date] = price


# Combine the two data sets

df = pd.DataFrame.from_dict(nysi_list)
# Insert 'Red / Black' column after 'NYSI value'
def red_black_logic(values):
    result = ['n/a']  # First row has no previous, default to n/a
    for i in range(1, len(values)):
        if values[i] > values[i-1]:
            result.append('Black')
        elif values[i] < values[i-1]:
            result.append('Red')
        else:
            # If NYSI is the same, maintain previous Red/Black value
            result.append(result[i-1])
    return result
df.insert(df.columns.get_loc('NYSI value') + 1, 'Red / Black', red_black_logic(df['NYSI value'].values))


# Map stock price by matching date + 1 day (NYSI date + 1 = stock trading date)
# Because NYSI is released after US market close, so NYSI value is used for next day's trading
from datetime import timedelta

def get_next_trading_day_price(nysi_date, max_days_ahead=7):
    """
    Get the stock price for the next available ASX trading day after NYSI date.
    Starts with NYSI date + 1 and increments until finding valid price data.
    
    Args:
        nysi_date: The NYSI date
        max_days_ahead: Maximum days to look ahead (default 7 to cover weekends + holidays)
    
    Returns:
        tuple: (price_date, stock_price)
    """
    for days_ahead in range(1, max_days_ahead + 1):
        check_date = (nysi_date + timedelta(days=days_ahead)).date()
        price = stock_price_dict.get(check_date, None)
        if price is not None:
            return check_date, price
    
    # If no price found within max_days_ahead, return None, None
    return None, None

# Apply the trading day logic
trading_day_results = df['date'].apply(lambda d: get_next_trading_day_price(d))
df['GGUS_price_date'] = trading_day_results.apply(lambda x: x[0])
df[f'Stock Price ({STOCK_PRICE_TYPE})'] = trading_day_results.apply(lambda x: x[1])

# Add blacklist indicator column
def is_blacklisted(stock_price_date):
    """Check if the stock price date falls within the blacklist period"""
    if BLACKLIST_START is None or BLACKLIST_END is None or stock_price_date is None:
        return False
    return BLACKLIST_START <= stock_price_date <= BLACKLIST_END

df['Blacklisted'] = df['GGUS_price_date'].apply(lambda d: is_blacklisted(d))

# Convert DataFrame to CSV string
csv_string = df.to_csv(index=False)

# Generate output file name
from datetime import datetime
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
indicator_clean = INDICATOR_TICKER.replace('$','')
stock_clean = STOCK_TICKER.replace('.','_')

if args.csv:
    # Output as CSV
    if args.output:
        output_path = args.output
    else:
        output_path = f'{indicator_clean}_{stock_clean}_{STOCK_MONTHS}_trading_sim_{timestamp}.csv'
    
    # Write CSV file
    df.to_csv(output_path, index=False)
    print(f'CSV file created: {output_path}')
else:
    # Output as HTML webpage (original functionality)
    # Read the HTML file
    orig_html_path = 'template_trading_simulation_visualisation.html'
    with open(orig_html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # Replace the csvData variable and info placeholders in the HTML file
    import re
    csv_pattern = r'(const csvData = `)[\s\S]*?(`;)'
    new_html = re.sub(csv_pattern, f"\\1{csv_string}\\2", html)
    new_html = new_html.replace('{{STOCK_TICKER}}', STOCK_TICKER)
    new_html = new_html.replace('{{STOCK_PRICE_TYPE}}', STOCK_PRICE_TYPE)
    new_html = new_html.replace('{{STOCK_MONTHS}}', str(STOCK_MONTHS))
    new_html = new_html.replace('{{BUY_SIGNAL}}', BUY_SIGNAL)
    new_html = new_html.replace('{{INDICATOR_TICKER}}', INDICATOR_TICKER)
    
    # Add blacklist information
    if BLACKLIST_START and BLACKLIST_END:
        new_html = new_html.replace('{{BLACKLIST_START}}', str(BLACKLIST_START))
        new_html = new_html.replace('{{BLACKLIST_END}}', str(BLACKLIST_END))
        new_html = new_html.replace('{{HAS_BLACKLIST}}', 'true')
    else:
        new_html = new_html.replace('{{BLACKLIST_START}}', '')
        new_html = new_html.replace('{{BLACKLIST_END}}', '')
        new_html = new_html.replace('{{HAS_BLACKLIST}}', 'false')

    if args.output:
        output_path = args.output
    else:
        output_path = f'{indicator_clean}_{stock_clean}_{STOCK_MONTHS}_trading_sim_{timestamp}.html'

    # Write the updated HTML file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f'HTML file created: {output_path}')
# Crap Stock Tools

This repository contains two main Python tools for stock market analysis:

- **[Crap Stock Monitor](#crap-stock-monitor)**: Monitors the NYSE McClellan Summation Index (NYSI) and sends email alerts when trend changes occur
- **[Trading Simulator](#trading-simulator)**: Generates trading simulation reports by combining NYSI data with stock price data to analyze trading strategies

---

## Crap Stock Monitor

Watches the NYSE McClellan Summation Index (NYSI) on StockCharts and emails you when it flips between **Red** (falling) and **Black** (rising). It can also post a one-line alert to a Signal group through the Koda Signal relay.

### How it works

- New NYSI data appears soon after the US market closes at 16:00 New York time. In Melbourne that's 08:00 (Nov–Mar), 06:00 (mid-year) or 07:00 (the weeks when only one country is on daylight saving). The monitor works this out itself, so there's no time window to configure.
- From each US close it checks every `--interval` seconds (default 2 minutes) for `--window-hours` (default 4, so 06:00–10:00 mid-year and 08:00–12:00 in summer). Once the new day's data is in, it sleeps until the next trading day's close.
- If the data hasn't appeared by the end of that window (a US holiday, or StockCharts running late) it checks every `--slow-interval` (default hourly) until it does, and is quick again from the next close.
- A day is **Black** if NYSI rose from the day before, **Red** if it fell. On a flat day it keeps the direction of the last real move (Red if there hasn't been one yet).
- If the colour differs from the last one it saw, it emails you. With `--email-frequency daily` it emails every new trading day instead, and says whether the colour changed.
- Every email shows the NYSI value, the change since the previous trading day, and the streak: how many trading days in a row it's been this colour (or, on a flip, how long the previous colour lasted). It downloads about 40 trading days, so longer streaks show as e.g. "40+ days". The subject line carries the value and change too, e.g. `NYSI Daily Update: Red -551.66 (-88.55)` or `NYSI Alert: Black → Red, -264.54 (-6.50)`.
- It remembers the last trading day and colour in `data/NYSI_state.json`, so restarts don't send duplicates. The first run just saves a starting point.
- If an email fails to send, the state isn't updated, so the same alert is retried on the next check.
- If Signal is set up, the same alert goes to Signal as one line, e.g. `🔴 NYSI flipped Black → Red: -264.54 (-6.50) on 2026-11-25. Black lasted 12 days.` `--signal-frequency` is separate from the email setting, so you can get daily emails while the group only hears about flips. Signal is best effort: it's sent after the email, has a 10-second timeout, and a failure is logged and not retried. Stale-data warnings go to email only.
- If no new data arrives for more than 5 days (StockCharts down, endpoint changed, etc.) it sends one warning email, and re-arms once data comes back.

### Installation

```bash
pip install -r requirements-monitor.txt
cp email_details.example.py email_details.py   # then fill it in
python crap_stock_monitor.py --check           # one check, then exit
```

### Usage

```bash
python crap_stock_monitor.py                     # keep watching
python crap_stock_monitor.py --interval 60       # check every minute after the US close
python crap_stock_monitor.py --email-frequency daily
python crap_stock_monitor.py --check             # one check and exit
python crap_stock_monitor.py --history           # recent days and their colours
python crap_stock_monitor.py --validate-email    # check the email login works
python crap_stock_monitor.py --test-email        # send a test email
python crap_stock_monitor.py --signal-destination task-mgmt --test-signal   # send a test Signal message
```

Every option can also be set with an environment variable (used by `docker-compose.yml`): `SYMBOL`, `CHECK_INTERVAL`, `FAST_WINDOW_HOURS`, `SLOW_CHECK_INTERVAL`, `DATA_DIR`, `EMAIL_FREQUENCY`, `SIGNAL_DESTINATION`, `SIGNAL_FREQUENCY`. The old `--window` / `MONITORING_WINDOW` setting is ignored.

### Configuration

Email credentials live in `email_details.py`, which is gitignored and dockerignored so it can never be committed or baked into the image. Copy the example and fill it in:

```python
sender_email = "your.email@gmail.com"
sender_password_password = "your-app-password"  # Gmail app password
recipients = ["recipient@example.com"]          # empty list = send to yourself
```

- **Running locally**: keep `email_details.py` next to `crap_stock_monitor.py`. It's found there whichever folder you run from.
- **Running in Docker**: the image ships without it. Mount it into the container (see `docker-compose.yml`), e.g. `/dockers/crap-stock-monitoring/email_details.py:/app/email_details.py`. Without the mount, email notifications are disabled.

Signal needs two things: `signal_details.py` (also gitignored and dockerignored) with the relay address, this client's name and its shared secret, and `SIGNAL_DESTINATION` set to one of the destination names the relay allows this client to post to. Without either, Signal is off.

```python
relay_url = "http://relay-host:3046"
client_name = "crap-stock-monitor"
secret = "shared-secret-from-the-relay"
```

Each request is signed with HMAC-SHA256 over `<timestamp>.<nonce>.<body>`, so the secret itself never crosses the network, and the relay rejects old or repeated requests. To post somewhere else, change `SIGNAL_DESTINATION`; the relay refuses destinations this client isn't allowed.

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests use fake data and a fake mail server, so they don't touch StockCharts or send email. They cover parsing, colour rules, the state file, one email per flip (including a replay with several flips), retrying failed emails, Signal messages (separate frequency, failures don't block, no double posts, signatures matching the relay), the stale-data warning, the SMTP timeout, and the check schedule (weekends, US holidays, late data, and both countries' daylight-saving changes).

---

## Trading Simulator

A Python script that fetches NYSI data from StockCharts and stock data from Yahoo Finance, merges the data by date, and generates trading simulation reports. The tool analyzes how trading strategies based on NYSI signals would perform with specific stocks.

### Setup

```bash
pip install -r requirements-simulator.txt
```

### System Requirements

**Environment:**

- Python 3.6+
- Internet connection

**Libraries:**

- `requests` (for StockCharts API)
- `pandas` (for data manipulation)
- `yfinance` (for stock data)
- Built-in: `datetime`, `argparse`

### Running Simulations

```bash
# Basic simulation with defaults (NYSI + GGUS.AX, 12 months)
python trading_simulator.py

# Custom stock and indicator with specific timeframe
python trading_simulator.py -i $NYSI -s GGUS.AX -m 12 -p Close

# Generate CSV output instead of HTML
python trading_simulator.py --csv -o data.csv -m 24

# Exclude trading during volatile periods
python trading_simulator.py -m 12 --blacklist-start 2024-12-20 --blacklist-end 2025-01-10
```

### Options

- `-i, --indicator`: Indicator ticker (default: $NYSI)
- `-s, --stock`: Stock ticker (default: GGUS.AX)
- `-b, --buy-signal`: Buy signal color (default: Black)
- `-m, --months`: Duration in months (default: 12)
- `-p, --price-type`: Stock price type (default: Close)
- `-o, --output`: Custom output filename
- `--csv`: Output as CSV instead of HTML
- `--blacklist-start/end`: Exclude trading during specified date range

### Output

The script generates either:

- **HTML**: Interactive webpage with trading simulation results (default)
- **CSV**: Data file for further analysis (with `--csv` flag)

Files are automatically named as: `INDICATOR_STOCK_MONTHS_trading_sim_YYYYMMDD_HHMMSS.html/csv`

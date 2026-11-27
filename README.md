# Crap Stock Tools

This repository contains two main Python tools for stock market analysis:

- **[Crap Stock Monitor](#crap-stock-monitor)**: Watches StockCharts indicators (NYSI, NYMO, ...) and sends email and Signal alerts when they flip colour or cross a level
- **[Trading Simulator](#trading-simulator)**: Generates trading simulation reports by combining NYSI data with stock price data to analyze trading strategies

---

## Crap Stock Monitor

Watches indicators on StockCharts and emails you when one of them does something you asked to hear about. It can also post a one-line alert to a Signal group through the Koda Signal relay. One container watches as many symbols as you like.

### Watches

A watch is a symbol, a rule, and how often to tell you. Set them with `WATCHES`, one per line:

```yaml
WATCHES: |
  NYSI  colour          change
  NYMO  levels -50 50   change
```

- **`colour`**: a day is **Black** if the value rose from the day before, **Red** if it fell. On a flat day it keeps the direction of the last real move (Red if there hasn't been one yet).
- **`levels <numbers>`**: which side of the levels the value is on. With `-50 50` a day is `below -50`, `between -50 and 50` or `above 50`, and it alerts on every cross in either direction: `NYMO crossed below -50` and later `NYMO crossed above -50`. A value exactly on a level counts as below it. One level works too, e.g. `NYSI levels 0` for the zero line.
- **`change`** (the default) sends only when the rule's answer changes. **`daily`** sends every new trading day and says whether it changed.

Lines can also be separated with `;` (handy on the command line), and `#` starts a comment. Each symbol is downloaded once per check however many watches use it, and a symbol can have one watch of each rule. Email and Signal settings are for the whole monitor, so every watch goes to the same places.

Without `WATCHES` it behaves as before v3.0: one colour watch on `SYMBOL` (default NYSI) at `EMAIL_FREQUENCY`.

Adding a rule means writing a small class like `ColourRule` or `LevelsRule` in `crap_stock_monitor.py` (a label for each day, how a change is described, an emoji) and adding it to `parse_watch`.

### How it works

- New data appears soon after the US market closes at 16:00 New York time. In Melbourne that's 08:00 (Nov–Mar), 06:00 (mid-year) or 07:00 (the weeks when only one country is on daylight saving). The monitor works this out itself, so there's no time window to configure.
- From each US close it checks every `--interval` seconds (default 2 minutes) for `--window-hours` (default 4, so 06:00–10:00 mid-year and 08:00–12:00 in summer). Once the new day's data is in, it sleeps until the next trading day's close.
- If the data hasn't appeared by the end of that window (a US holiday, or StockCharts running late) it checks every `--slow-interval` (default hourly) until it does, and is quick again from the next close.
- For each watch, if the newest day's answer differs from the last one it saw, it emails you (or every day, for a `daily` watch).
- Every email shows the value, the change since the previous trading day, and the streak: how many trading days in a row it's had this answer (or, on a change, how long the previous one lasted). It downloads about 40 trading days, so longer streaks show as e.g. "40+ days". The subject line carries the value and change too, e.g. `NYSI Alert: Black → Red, -264.54 (-6.50)`, `NYMO Alert: crossed below -50, -62.40 (-20.60)` or `NYSI Daily Update: Red, -551.66 (-88.55)`.
- Each watch remembers its last trading day and answer in a state file (`data/NYSI_state.json` for a colour watch, `data/NYMO_levels_state.json` for a levels watch), so restarts don't send duplicates. The first run just saves a starting point. If you change a watch's levels, it starts afresh the same way instead of alerting.
- If an email fails to send, the state isn't updated, so the same alert is retried on the next check.
- If Signal is set up, the same alert goes to Signal as one line, e.g. `🔴 NYSI Black → Red: -264.54 (-6.50) on 2026-11-25. Black lasted 12 days.` or `📈 NYMO crossed above -50: -39.07 (+31.84) on 2026-11-23. Below -50 lasted 2 days.` Signal is best effort: it's sent after the email, has a 10-second timeout, and a failure is logged and not retried. Stale-data warnings go to email only.
- It keeps the whole history of each symbol in `data/<SYMBOL>_history.csv` (StockCharts has real NYSI and NYMO data from June 1998). Every check merges its download into that file, which picks up any late correction to recent days. The full history is downloaded on first start and again once a month. Only dates and values are stored; colours and streaks are worked out when asked.
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
python crap_stock_monitor.py --watches "NYSI colour change; NYMO levels -50 50 change"
python crap_stock_monitor.py --email-frequency daily    # without --watches: one NYSI colour watch
python crap_stock_monitor.py --check             # one check and exit
python crap_stock_monitor.py --history           # recent days for each watch
python crap_stock_monitor.py --validate-email    # check the email login works
python crap_stock_monitor.py --test-email        # send a test email
python crap_stock_monitor.py --signal-destination smtmc --test-signal   # send a test Signal message
```

Every option can also be set with an environment variable (used by `docker-compose.yml`): `WATCHES`, `SYMBOL`, `EMAIL_FREQUENCY`, `CHECK_INTERVAL`, `FAST_WINDOW_HOURS`, `SLOW_CHECK_INTERVAL`, `DATA_DIR`, `SIGNAL_DESTINATION`, `HTTP_PORT`. `SYMBOL` and `EMAIL_FREQUENCY` only matter without `WATCHES`. The old `--window` / `MONITORING_WINDOW` and `SIGNAL_FREQUENCY` settings are ignored: since v3.0 each watch's frequency applies to email and Signal alike. The old frequency names `colour_change` and `new_data` still work.

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

### History endpoint

With `HTTP_PORT` set (e.g. `8095`), the monitor serves the history of every watched symbol read-only. It only reads the history file, so questions never trigger a StockCharts download.

```
GET /health                       {"ok": true, "symbols": ["NYSI", "NYMO"]}
GET /history?symbol=NYSI                      the last 365 calendar days
GET /history?symbol=NYMO&days=90              the last 90 calendar days
GET /history?symbol=NYSI&days=all             everything since 1998
GET /history?symbol=NYSI&days=all&summary=1   leave out the per-day list
```

`symbol` is required and must be one of the watched symbols: without it the answer is 400, and an unwatched symbol is 404. Both errors list the watched symbols.

The response has `latest` (date, value, change, colour, streak; colour here is always the daily Black/Red direction, whatever the watch's rule), `window`, `days` (date, value, change and colour for each trading day in the window) and `runs`: each Red or Black streak that touches the window, with its start, end and full length in trading days. A run that began before the window keeps its full length and has `starts_before_window: true`; the current one has `ongoing: true`. So "the longest Red streak in the last year" is the largest Red `trading_days` in `/history?symbol=NYSI&days=365&summary=1`. `data_starts` says where the stored data begins.

There's no authentication: the data is public market data. Only publish the port on a network you trust.

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests use fake data and a fake mail server, so they don't touch StockCharts or send email. They cover parsing, the `WATCHES` setting, the colour and levels rules (including a NYMO replay crossing out of the band and back), one download per symbol, the state file (including pre-v3.0 state and changed levels), one email per flip (including a replay with several flips), retrying failed emails, Signal messages (separate frequency, failures don't block, no double posts, signatures matching the relay), the history file (full download, monthly refresh, merging, bad rows), the history report and HTTP endpoint, the stale-data warning, the SMTP timeout, and the check schedule (weekends, US holidays, late data, and both countries' daylight-saving changes).

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

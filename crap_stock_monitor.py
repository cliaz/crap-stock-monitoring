#!/usr/bin/env python3
"""
Crap Stock Monitor

Watches indicators on StockCharts (NYSI, NYMO, ...) and emails you when one
of them does something you asked to hear about. It can also post a short
message to a Signal group through the Koda Signal relay.

Each watch is a symbol, a rule and how often to tell you:
  - colour: a day is Black if the value rose, Red if it fell. Alerts on a flip.
  - levels: alerts when the value crosses one of the given levels, in either
    direction (e.g. NYMO below -50, and back above it).
  - change (only when the rule's answer changes) or daily (every trading day).
Email and Signal settings are for the whole monitor; every watch uses them.

How it works:
  - New data appears soon after the US market closes (16:00 New York
    time, which is 06:00-08:00 in Melbourne depending on daylight saving).
  - From each US close it checks every --interval seconds (default 2 minutes)
    for --window-hours (default 4). Once the new day's data is in, it sleeps
    until the next trading day's close.
  - If the data hasn't appeared by the end of the window (US holiday,
    StockCharts running late) it checks every --slow-interval (default
    hourly) until it does, and is quick again at the next close.
  - Each symbol is downloaded once per check, however many watches use it.
  - When a new trading day appears, each watch works out that day's answer
    (a colour, or which side of the levels the value is on).
  - If the answer differs from the last one it saw, it sends an email, and a
    Signal message if signal_details.py is set up. Signal is best effort: if
    the relay doesn't answer within 10 seconds it logs the failure and moves on.
  - Each watch remembers its last day and answer in a small state file, so a
    restart doesn't send duplicate alerts.
  - If no new data turns up for several days (StockCharts down, endpoint
    changed, etc.) it sends one warning email so a silent failure gets noticed.
  - It keeps the whole history of each symbol (NYSI goes back to mid-1998) in
    data/<SYMBOL>_history.csv, merging in every download, and can serve it
    over HTTP (GET /history?symbol=NYMO) so other tools can ask about past
    values and streaks.
"""

import argparse
import csv
import hashlib
import hmac
import importlib.util
import json
import math
import os
import secrets
import smtplib
import sys
import threading
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, time as clock_time, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from zoneinfo import ZoneInfo

import requests


SCRIPT_DIR = Path(__file__).resolve().parent

STOCKCHARTS_URL = "https://stockcharts.com/c-sc/sc"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
DAYS_TO_FETCH = 60            # calendar days (~40 trading days): enough for the last move and the streak
HTTP_TIMEOUT_SECONDS = 15

HISTORY_FULL_DAYS = 20000          # calendar days, more than StockCharts has (real data starts mid-1998)
HISTORY_REFRESH_DAYS = 30          # re-download everything this often, in case old values were corrected
DEFAULT_WINDOW_DAYS = 365          # what GET /history returns unless asked for more

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 30

SIGNAL_TIMEOUT_SECONDS = 10

# US data for Friday arrives on Saturday morning in Australia, so a normal
# weekend means 3 days without new data, a long weekend 4-5. Six or more
# days means something is wrong.
STALE_AFTER_DAYS = 5

US_MARKET_TIMEZONE = ZoneInfo("America/New_York")
US_MARKET_CLOSE = clock_time(16, 0)

FREQUENCIES = ["change", "daily"]
OLD_FREQUENCY_NAMES = {"colour_change": "change", "new_data": "daily"}   # names used before v3.0
BLACK = "Black"   # the value went up
RED = "Red"       # the value went down


def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Fetching and parsing data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DataPoint:
    day: date
    value: float


def normalise_symbol(symbol):
    """'$nysi', ' NYSI ' and 'NYSI' all become 'NYSI'."""
    return symbol.strip().lstrip("$").upper()


def build_stockcharts_url(symbol, days):
    symbol = normalise_symbol(symbol)
    return f"{STOCKCHARTS_URL}?s=${symbol}&p=D&yr=0&mn=0&dy={days}&i=t3757734781c&img=text&inspector=yes"


def parse_stockcharts(text):
    """
    Turn a StockCharts text response into a date-sorted list of DataPoints.

    The useful part looks like:
        <pricedata>83 202509180930 202509181600 631.29 0|218 202509190930 ...</pricedata>
    Each '|'-separated row is: index, start time, end time, value, volume.
    """
    if "<pricedata>" not in text:
        raise ValueError("StockCharts response has no <pricedata> block")

    body = text.split("<pricedata>", 1)[1]
    body = body.split("</pricedata>", 1)[0]

    result = []
    for row in body.split("|"):
        columns = row.strip().split(" ")
        if len(columns) < 4:
            continue
        try:
            day = datetime.strptime(columns[1][:8], "%Y%m%d").date()
            value = float(columns[3])
        except ValueError:
            continue
        if math.isnan(value):
            continue
        result.append(DataPoint(day, value))

    result.sort(key=lambda point: point.day)
    return result


def fetch_indicator(symbol, days):
    """Download and parse the last `days` days of an indicator. Raises on failure."""
    url = build_stockcharts_url(symbol, days)
    response = requests.get(url, headers={"user-agent": USER_AGENT}, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return parse_stockcharts(response.text)


# ---------------------------------------------------------------------------
# Working out the colour
# ---------------------------------------------------------------------------

def colour_at(data, index):
    """
    Colour of the day at `index`: Black if it rose from the day before, Red if
    it fell. On a flat day, keep the direction of the most recent real move.
    If there has been no move at all yet, default to Red.
    """
    for i in range(index, 0, -1):
        today_value = data[i].value
        previous_value = data[i - 1].value
        if today_value > previous_value:
            return BLACK
        if today_value < previous_value:
            return RED
    return RED


def latest_colour(data):
    if len(data) < 2:
        return None
    return colour_at(data, len(data) - 1)


def colour_history(data):
    """List of (DataPoint, colour) for every day that has a day before it."""
    return [(data[i], colour_at(data, i)) for i in range(1, len(data))]


def daily_change(data):
    """How much the newest value moved since the trading day before it, or None."""
    if len(data) < 2:
        return None
    return data[-1].value - data[-2].value


def streak(history):
    """
    How many trading days in a row the newest label in a list of
    (DataPoint, label) pairs has lasted, counting the newest day. Returns
    (days, complete). `complete` is False when the streak runs right back to
    the start of the list, so it may be longer.
    """
    if not history:
        return 0, False

    newest_label = history[-1][1]
    days = 0
    for _, label in reversed(history):
        if label != newest_label:
            return days, True
        days += 1
    return days, False


def colour_streak(data):
    """How many trading days in a row the newest colour has lasted. See streak()."""
    return streak(colour_history(data))


# ---------------------------------------------------------------------------
# Rules: what a watch looks for
# ---------------------------------------------------------------------------
#
# A rule gives each trading day a label (a colour, or a side of the levels).
# The monitor alerts when the newest day's label differs from the last one
# it saw. A new rule needs the same five things as these two.

COLOUR_EMOJI = {BLACK: "⚫", RED: "🔴"}


class ColourRule:
    """Black if the value rose from the day before, Red if it fell."""

    name = "colour"
    heading = "Colour"

    def describe(self):
        return "colour"

    def label_at(self, data, index):
        return colour_at(data, index)

    def transition(self, old_label, new_label):
        """How a change is described, e.g. 'Black → Red'."""
        return f"{old_label} → {new_label}"

    def emoji(self, old_label, new_label):
        return COLOUR_EMOJI.get(new_label, "")


class LevelsRule:
    """
    Which side of the levels the value is on. With levels -50 and 50 a day is
    'below -50', 'between -50 and 50' or 'above 50'. A value exactly on a level
    counts as below it.
    """

    name = "levels"
    heading = "Level"

    def __init__(self, levels):
        self.levels = sorted(levels)
        names = [f"{level:g}" for level in self.levels]
        self.labels = [f"below {names[0]}"]
        self.labels += [f"between {low} and {high}" for low, high in zip(names, names[1:])]
        self.labels += [f"above {names[-1]}"]

    def describe(self):
        return "levels " + " ".join(f"{level:g}" for level in self.levels)

    def label_at(self, data, index):
        levels_below = sum(1 for level in self.levels if data[index].value > level)
        return self.labels[levels_below]

    def transition(self, old_label, new_label):
        """Which level was crossed, e.g. 'crossed below -50'."""
        old_zone = self.labels.index(old_label)
        new_zone = self.labels.index(new_label)
        if new_zone > old_zone:
            return f"crossed above {self.levels[new_zone - 1]:g}"
        return f"crossed below {self.levels[new_zone]:g}"

    def emoji(self, old_label, new_label):
        if old_label == new_label:
            return "📊"
        return "📈" if self.labels.index(new_label) > self.labels.index(old_label) else "📉"


# ---------------------------------------------------------------------------
# Watches
# ---------------------------------------------------------------------------
#
# The WATCHES setting has one watch per line (or separated by ';'):
#     NYSI  colour          change
#     NYMO  levels -50 50   change
# The last word is how often to send (change or daily, default change).

@dataclass(frozen=True)
class Watch:
    symbol: str
    rule: object
    frequency: str = "change"

    def describe(self):
        return f"{self.symbol} {self.rule.describe()} {self.frequency}"

    def labels(self, data):
        """List of (DataPoint, label) for every day that has a day before it."""
        return [(data[i], self.rule.label_at(data, i)) for i in range(1, len(data))]

    def latest_label(self, data):
        if len(data) < 2:
            return None
        return self.rule.label_at(data, len(data) - 1)


def parse_frequency(text):
    """'change' or 'daily'. The old names colour_change and new_data still work."""
    text = OLD_FREQUENCY_NAMES.get(text, text)
    if text not in FREQUENCIES:
        raise ValueError(f"'{text}' isn't a frequency; use change or daily")
    return text


def parse_watch(line):
    """Turn one line like 'NYMO levels -50 50 change' into a Watch. Raises ValueError."""
    words = line.split()
    if len(words) < 2:
        raise ValueError(f"'{line}': needs at least a symbol and a rule")

    symbol, rule_name, rest = normalise_symbol(words[0]), words[1].lower(), words[2:]
    frequency = "change"
    if rest and not is_number(rest[-1]):
        frequency = parse_frequency(rest.pop())

    if rule_name == "colour":
        if rest:
            raise ValueError(f"'{line}': colour doesn't take any numbers")
        rule = ColourRule()
    elif rule_name == "levels":
        if not rest or not all(is_number(word) for word in rest):
            raise ValueError(f"'{line}': levels needs one or more numbers, e.g. levels -50 50")
        rule = LevelsRule(float(word) for word in rest)
    else:
        raise ValueError(f"'{line}': unknown rule '{rule_name}'; use colour or levels")
    return Watch(symbol, rule, frequency)


def is_number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


def parse_watches(text):
    """Parse the WATCHES setting. Blank lines and '#' comments are skipped. Raises ValueError."""
    watches = []
    for line in text.replace(";", "\n").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            watches.append(parse_watch(line))

    if not watches:
        raise ValueError("WATCHES is set but has no watches in it")
    seen = set()
    for watch in watches:
        key = (watch.symbol, watch.rule.name)
        if key in seen:
            raise ValueError(f"{watch.symbol} has two {watch.rule.name} watches; combine them into one line")
        seen.add(key)
    return watches


def state_file_name(watch):
    """Colour watches keep the pre-v3.0 name (NYSI_state.json), so existing state carries over."""
    if watch.rule.name == "colour":
        return f"{watch.symbol}_state.json"
    return f"{watch.symbol}_{watch.rule.name}_state.json"


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

@dataclass
class State:
    last_date: str = None         # 'YYYY-MM-DD' of the last trading day processed
    last_label: str = None        # the rule's answer that day, e.g. 'Red' or 'below -50'
    last_value: float = None
    stale_alert_sent: bool = False
    rule: str = None              # e.g. 'levels -50 50'; if the rule changes, start afresh


def load_state(path):
    """Read the state file. A missing or unreadable file gives an empty State."""
    path = Path(path)
    if not path.exists():
        return State()

    try:
        saved = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        log(f"WARNING: could not read state file {path}: {error}. Starting fresh.")
        return State()

    # Before v3.0 the label was 'last_colour' ('last_color' in v2.5 and earlier),
    # and there was no rule because colour was the only one.
    return State(
        last_date=saved.get("last_date"),
        last_label=saved.get("last_label") or saved.get("last_colour") or saved.get("last_color"),
        last_value=saved.get("last_value"),
        stale_alert_sent=saved.get("stale_alert_sent", False),
        rule=saved.get("rule"),
    )


def save_state(path, state):
    """Write the state file. Writes to a temp file first so a crash can't leave half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = asdict(state)
    contents["updated"] = datetime.now().isoformat(timespec="seconds")

    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(contents, indent=2))
    os.replace(temp_path, path)


# ---------------------------------------------------------------------------
# History file
# ---------------------------------------------------------------------------
#
# A CSV of every day's value, oldest first:
#     # full download: 2026-11-27
#     date,value
#     1998-05-20,123.45
# Only dates and values are stored. Colours and streaks are worked out when
# asked, so a change to the colour rules never leaves stale colours behind.

def load_history(path):
    """Read the history file. Returns (points, full_download_day); ([], None) if missing or unreadable."""
    path = Path(path)
    if not path.exists():
        return [], None

    points = []
    full_download_day = None
    try:
        with path.open(newline="") as file:
            for row in csv.reader(file):
                if not row or row[0] == "date":
                    continue
                if row[0].startswith("# full download:"):
                    full_download_day = date.fromisoformat(row[0].split(":", 1)[1].strip())
                    continue
                try:
                    points.append(DataPoint(date.fromisoformat(row[0]), float(row[1])))
                except (ValueError, IndexError):
                    continue
    except (OSError, ValueError) as error:
        log(f"WARNING: could not read history file {path}: {error}. Will download it again.")
        return [], None

    points.sort(key=lambda point: point.day)
    return points, full_download_day


def save_history(path, points, full_download_day):
    """Write the history file via a temp file, so a crash can't leave half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    with temp_path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([f"# full download: {full_download_day.isoformat()}"])
        writer.writerow(["date", "value"])
        for point in points:
            writer.writerow([point.day.isoformat(), point.value])
    os.replace(temp_path, path)


def merge_points(old, new):
    """Combine two lists of DataPoints by day. Where both have a day, `new` wins."""
    by_day = {point.day: point for point in old}
    by_day.update({point.day: point for point in new})
    return [by_day[day] for day in sorted(by_day)]


def update_history(path, symbol, recent, fetch, today):
    """
    Merge the latest download into the history file. Downloads the full
    history if there isn't one yet, or every HISTORY_REFRESH_DAYS. Never
    raises: the history is a bonus and must not get in the way of alerts.
    """
    try:
        points, full_download_day = load_history(path)
        due = full_download_day is None or (today - full_download_day).days >= HISTORY_REFRESH_DAYS
        if not points or due:
            try:
                points = fetch(symbol, HISTORY_FULL_DAYS)
                full_download_day = today
                log(f"Downloaded the full {symbol} history: {len(points)} days from {points[0].day}")
            except Exception as error:
                log(f"WARNING: could not download the full {symbol} history: {error}")
                if full_download_day is None:
                    full_download_day = today - timedelta(days=HISTORY_REFRESH_DAYS)   # try again next check

        merged = merge_points(points, recent)
        if merged != points or due:
            save_history(path, merged, full_download_day)
    except Exception as error:
        log(f"WARNING: could not update the history file {path}: {error}")


# ---------------------------------------------------------------------------
# History report (served over HTTP)
# ---------------------------------------------------------------------------

def colour_runs(history):
    """Group (DataPoint, colour) pairs into runs: [colour, first point, last point, trading days]."""
    runs = []
    for point, colour in history:
        if runs and runs[-1][0] == colour:
            runs[-1][2] = point
            runs[-1][3] += 1
        else:
            runs.append([colour, point, point, 1])
    return runs


def build_report(symbol, points, window_days=DEFAULT_WINDOW_DAYS, summary=False):
    """
    Everything a client needs to answer questions about the series, as a dict
    ready for JSON. `window_days` is calendar days back from the newest day,
    or None for all of it. Runs (streaks) are measured over the whole history,
    so a run that started before the window still shows its full length.
    """
    if len(points) < 2:
        return None

    history = colour_history(points)
    latest = points[-1]
    window_start = points[1].day if window_days is None else latest.day - timedelta(days=window_days)
    streak_days, streak_complete = colour_streak(points)

    days = []
    for i in range(1, len(points)):
        if points[i].day >= window_start:
            days.append({
                "date": points[i].day.isoformat(),
                "value": round(points[i].value, 2),
                "change": round(points[i].value - points[i - 1].value, 2),
                "colour": history[i - 1][1],
            })

    runs = []
    all_runs = colour_runs(history)
    for index, (colour, first, last, trading_days) in enumerate(all_runs):
        if last.day < window_start:
            continue
        runs.append({
            "colour": colour,
            "start": first.day.isoformat(),
            "end": last.day.isoformat(),
            "trading_days": trading_days,
            "starts_before_window": first.day < window_start,
            "starts_at_data_start": index == 0,
            "ongoing": index == len(all_runs) - 1,
        })

    report = {
        "symbol": symbol,
        "data_starts": points[0].day.isoformat(),
        "latest": {
            "date": latest.day.isoformat(),
            "value": round(latest.value, 2),
            "change": round(daily_change(points), 2),
            "colour": history[-1][1],
            "streak_trading_days": streak_days,
            "streak_complete": streak_complete,
        },
        "window": {
            "from": window_start.isoformat(),
            "to": latest.day.isoformat(),
            "calendar_days": window_days,
            "trading_days": len(days),
        },
        "runs": runs,
        "colour_rule": "Black if the value rose from the previous trading day, Red if it fell; "
                       "a flat day keeps the previous colour",
    }
    if not summary:
        report["days"] = days
    return report


def history_path_for(data_dir, symbol):
    return Path(data_dir) / f"{symbol}_history.csv"


def make_handler(data_dir, symbols):
    class HistoryHandler(BaseHTTPRequestHandler):
        def send_json(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":
                self.send_json(200, {"ok": True, "symbols": symbols})
                return
            if url.path != "/history":
                self.send_json(404, {"error": f"not found; try /history?symbol={symbols[0]}&days=365"})
                return

            query = parse_qs(url.query)
            if not query.get("symbol"):
                self.send_json(400, {"error": f"symbol is required, one of {', '.join(symbols)}"})
                return
            symbol = normalise_symbol(query["symbol"][0])
            if symbol not in symbols:
                self.send_json(404, {"error": f"{symbol} isn't watched; try one of {', '.join(symbols)}"})
                return
            days_text = query.get("days", [str(DEFAULT_WINDOW_DAYS)])[0]
            if days_text == "all":
                window_days = None
            elif days_text.isdigit() and int(days_text) > 0:
                window_days = int(days_text)
            else:
                self.send_json(400, {"error": "days must be a positive number or 'all'"})
                return
            summary = query.get("summary", ["0"])[0] in ("1", "true", "yes")

            points, _ = load_history(history_path_for(data_dir, symbol))
            report = build_report(symbol, points, window_days, summary)
            if report is None:
                self.send_json(503, {"error": "no history yet; the monitor fills it on its next check"})
                return
            self.send_json(200, report)

        def log_message(self, format, *args):
            log(f"HTTP {self.address_string()} {format % args}")

    return HistoryHandler


def start_http_server(port, data_dir, symbols):
    """Serve the history of every watched symbol read-only in a background thread. Returns the server."""
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(data_dir, symbols))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"Serving {', '.join(symbols)} history on port {server.server_address[1]} "
        f"(GET /history?symbol={symbols[0]}&days=365)")
    return server


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

class Mailer:
    def __init__(self, sender, password, recipients):
        self.sender = sender
        self.password = password
        self.recipients = list(recipients) if recipients else [sender]

    def _connect(self):
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS)
        return server

    def validate(self):
        """Log in to the mail server without sending anything. Raises on failure."""
        with self._connect() as server:
            server.starttls()
            server.login(self.sender, self.password)

    def send(self, subject, html):
        """Send an HTML email to all recipients. Raises on failure."""
        message = MIMEMultipart()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = subject
        message.attach(MIMEText(html, "html"))

        with self._connect() as server:
            server.starttls()
            server.login(self.sender, self.password)
            server.send_message(message)


def load_mailer(config_path=SCRIPT_DIR / "email_details.py"):
    """
    Build a Mailer from email_details.py, or return None if there isn't one.
    The file is looked up next to this script, not in the current folder.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("email_details", config_path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)

    sender = getattr(config, "sender_email", None)
    password = getattr(config, "sender_password_password", None)
    if not sender or not password:
        log(f"WARNING: {config_path} is missing sender_email or sender_password_password")
        return None

    return Mailer(sender, password, getattr(config, "recipients", []))


# ---------------------------------------------------------------------------
# Signal (through the Koda Signal relay)
# ---------------------------------------------------------------------------

class SignalRelay:
    """
    Posts plain-text messages to a Signal group through the Koda Signal relay.

    Each request is signed with a shared secret (HMAC-SHA256 over
    "<timestamp>.<nonce>.<body>"), so the secret itself never goes over the
    network. The relay decides which destinations this client may post to.
    """

    def __init__(self, url, client, secret, destination):
        self.url = url.rstrip("/")
        self.client = client
        self.secret = secret
        self.destination = destination

    def signed_headers(self, body, timestamp=None, nonce=None):
        timestamp = timestamp or str(int(time.time()))
        nonce = nonce or secrets.token_hex(12)
        signed = f"{timestamp}.{nonce}.".encode() + body
        signature = hmac.new(self.secret.encode(), signed, hashlib.sha256).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Relay-Client": self.client,
            "X-Relay-Timestamp": timestamp,
            "X-Relay-Nonce": nonce,
            "X-Relay-Signature": signature,
        }

    def send(self, text):
        """Post `text` to the destination. Raises on failure."""
        body = json.dumps({"to": self.destination, "message": text}).encode()
        response = requests.post(f"{self.url}/send", data=body, headers=self.signed_headers(body),
                                 timeout=SIGNAL_TIMEOUT_SECONDS)
        if response.status_code != 200:
            raise RuntimeError(f"relay answered {response.status_code}: {response.text.strip()}")


def load_signal(destination, config_path=SCRIPT_DIR / "signal_details.py"):
    """
    Build a SignalRelay from signal_details.py plus the destination, or return
    None if Signal isn't set up. Like email_details.py, the file sits next to
    this script and holds the secret, so it's never committed or baked in.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        return None
    if not destination:
        log(f"WARNING: {config_path} exists but SIGNAL_DESTINATION isn't set, so Signal is off")
        return None

    spec = importlib.util.spec_from_file_location("signal_details", config_path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)

    url = getattr(config, "relay_url", None)
    client = getattr(config, "client_name", None)
    secret = getattr(config, "secret", None)
    if not url or not client or not secret:
        log(f"WARNING: {config_path} is missing relay_url, client_name or secret")
        return None

    return SignalRelay(url, client, secret, destination)


def format_change(change):
    """'+12.34', '-5.00', or '0.00' for no change."""
    if change == 0:
        return "0.00"
    return f"{change:+.2f}"


def format_streak(days, complete):
    """'4 days', '2+ days', '1 day', '1+ day'."""
    plus = "" if complete else "+"
    unit = "day" if days == 1 else "days"
    return f"{days}{plus} {unit}"


def capitalise(text):
    return text[:1].upper() + text[1:]


def build_email(kind, watch, data=None, old_label=None, new_label=None, last_seen=None):
    """
    Return (subject, html) for an email. `kind` is 'change', 'daily' or
    'stale'. For the first two, `data` is the downloaded series (newest last).
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    symbol = watch.symbol

    if kind == "stale":
        subject = f"{symbol} Monitor: No new data since {last_seen}"
        body = (
            f"<p>The monitor has not seen any new {symbol} data since <strong>{last_seen}</strong>.</p>"
            f"<p>StockCharts may be down, or the data endpoint may have changed. "
            f"Check the container logs.</p>"
        )
    else:
        latest = data[-1]
        previous = data[-2]
        history = watch.labels(data)
        change = format_change(daily_change(data))
        changed = old_label != new_label
        value_and_change = f"{latest.value:.2f} ({change})"

        if changed:
            what = watch.rule.transition(old_label, new_label)
            prefix = "Alert" if kind == "change" else "Daily Update"
            subject = f"{symbol} {prefix}: {what}, {value_and_change}"
        else:
            subject = f"{symbol} Daily Update: {new_label}, {value_and_change}"

        change_line = f"{change} since {previous.day.isoformat()}"
        if change == "0.00":
            change_line += f" (flat, stays {new_label})"

        if changed:
            previous_days, previous_complete = streak(history[:-1])
            previous_run = format_streak(previous_days, previous_complete)
            label_line = f"{watch.rule.transition(old_label, new_label)}, after {previous_run} {old_label}"
        else:
            days, complete = streak(history)
            label_line = f"{new_label} for {format_streak(days, complete)} in a row"

        body = (
            f"<p><strong>{symbol} value:</strong> {latest.value:.2f} on {latest.day.isoformat()}</p>"
            f"<p><strong>Change:</strong> {change_line}</p>"
            f"<p><strong>{watch.rule.heading}:</strong> {label_line}</p>"
        )

    html = (
        f"<html><body>"
        f"<h2>{subject}</h2>"
        f"{body}"
        f"<p><small>Sent {now} by Crap Stock Monitor.</small></p>"
        f"</body></html>"
    )
    return subject, html


def build_signal_message(watch, data, old_label, new_label):
    """A one-line plain-text version of the alert, for Signal."""
    latest = data[-1]
    history = watch.labels(data)
    value_and_change = f"{latest.value:.2f} ({format_change(daily_change(data))})"
    emoji = watch.rule.emoji(old_label, new_label)

    if old_label != new_label:
        previous_days, previous_complete = streak(history[:-1])
        return (f"{emoji} {watch.symbol} {watch.rule.transition(old_label, new_label)}: {value_and_change} "
                f"on {latest.day.isoformat()}. "
                f"{capitalise(old_label)} lasted {format_streak(previous_days, previous_complete)}.")

    days, complete = streak(history)
    return (f"{emoji} {watch.symbol} {value_and_change} on {latest.day.isoformat()}, "
            f"{new_label} for {format_streak(days, complete)} in a row.")


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------

def is_stale(last_data_day, today):
    return (today - last_data_day).days > STALE_AFTER_DAYS


def try_send(mailer, subject, html):
    """Send an email. Returns True if sent, or if email isn't configured (nothing to retry)."""
    if mailer is None:
        log(f"Email not configured, would have sent: {subject}")
        return True
    try:
        mailer.send(subject, html)
        log(f"Email sent: {subject}")
        return True
    except Exception as error:
        log(f"ERROR: failed to send email '{subject}': {error}")
        return False


def try_send_signal(signal, text):
    """Post to Signal if it's set up. Never raises: a failure is logged and skipped."""
    if signal is None:
        return
    try:
        signal.send(text)
        log(f"Signal message sent to {signal.destination}")
    except Exception as error:
        log(f"ERROR: failed to send Signal message to {signal.destination}: {error}")


def check_for_stale_data(state, last_data_day, mailer, watch, today):
    """Send one warning when data stops arriving. Returns True if the state changed."""
    if last_data_day is None or state.stale_alert_sent:
        return False
    if not is_stale(last_data_day, today):
        return False

    subject, html = build_email("stale", watch, last_seen=last_data_day.isoformat())
    if try_send(mailer, subject, html):
        state.stale_alert_sent = True
        return True
    return False


def check_watch(watch, data, state_path, mailer, signal=None, today=None):
    """
    One watch's part of a check, given its symbol's latest download (None if
    the download failed). If there's a new trading day, update the state and
    send whatever email and Signal message are due. The state only moves
    forward once the email has been sent, so a failed email is retried on the
    next check. Signal is sent after the email, so a retried email doesn't
    post to Signal twice; a failed Signal post is not retried.
    """
    today = today or date.today()
    state = load_state(state_path)
    saved_day = date.fromisoformat(state.last_date) if state.last_date else None

    if data is None:
        if check_for_stale_data(state, saved_day, mailer, watch, today):
            save_state(state_path, state)
        return

    label = watch.latest_label(data)
    if label is None:
        log(f"WARNING: not enough {watch.symbol} data for its {watch.rule.name} watch ({len(data)} rows)")
        return

    latest = data[-1]
    rule = watch.rule.describe()

    # First ever run, or the rule has changed (e.g. new levels): remember
    # where we are, don't email.
    if state.last_date is None or (state.rule is not None and state.rule != rule):
        if state.last_date is not None:
            log(f"{watch.symbol} rule changed from '{state.rule}' to '{rule}', so starting afresh")
        log(f"First run: {watch.symbol} {latest.value} on {latest.day} is {label}. Saved as the starting point.")
        save_state(state_path, State(latest.day.isoformat(), label, latest.value, rule=rule))
        return

    # Nothing new yet.
    if latest.day <= saved_day:
        log(f"No new {watch.symbol} data yet (latest is {latest.day}, {state.last_label})")
        if check_for_stale_data(state, latest.day, mailer, watch, today):
            save_state(state_path, state)
        return

    # A new trading day.
    old_label = state.last_label
    changed = label != old_label
    log(f"New data: {watch.symbol} {latest.value} on {latest.day} is {label} (was {old_label})")

    if changed or watch.frequency == "daily":
        kind = "change" if changed and watch.frequency == "change" else "daily"
        subject, html = build_email(kind, watch, data, old_label, label)
        if not try_send(mailer, subject, html):
            log("State not updated, so the email will be retried on the next check")
            return
        try_send_signal(signal, build_signal_message(watch, data, old_label, label))

    save_state(state_path, State(latest.day.isoformat(), label, latest.value, rule=rule))


def run_once(watches, data_dir, mailer, fetch=fetch_indicator, today=None, signal=None, keep_history=False):
    """
    Do one check of every watch. Each symbol is downloaded once, however many
    watches use it. With `keep_history`, every download is merged into that
    symbol's history file. A problem with one watch doesn't stop the others.
    """
    today = today or date.today()
    symbols = list(dict.fromkeys(watch.symbol for watch in watches))   # in order, no repeats

    for symbol in symbols:
        try:
            data = fetch(symbol, DAYS_TO_FETCH)
        except Exception as error:
            log(f"ERROR: could not fetch {symbol} data: {error}")
            data = None

        if data is not None and keep_history:
            update_history(history_path_for(data_dir, symbol), symbol, data, fetch, today)

        for watch in watches:
            if watch.symbol != symbol:
                continue
            try:
                check_watch(watch, data, Path(data_dir) / state_file_name(watch), mailer, signal, today)
            except Exception as error:
                log(f"ERROR: unexpected problem checking {watch.describe()}: {error}")


def oldest_data_day(watches, data_dir):
    """
    The oldest last-seen trading day across the watches, so the schedule
    keeps checking until every symbol is up to date. None if none have data yet.
    """
    days = []
    for watch in watches:
        state = load_state(Path(data_dir) / state_file_name(watch))
        if state.last_date:
            days.append(date.fromisoformat(state.last_date))
    return min(days) if days else None


# ---------------------------------------------------------------------------
# Scheduling: when to check next
# ---------------------------------------------------------------------------
#
# All times here are timezone-aware, so daylight saving in New York and
# Melbourne (which change on different dates) is handled by zoneinfo.
# US public holidays aren't known; on a holiday the expected data just
# doesn't arrive, and the slow polling below covers it.

def us_close(trading_day):
    """The US market close on `trading_day`, as a UTC datetime."""
    close = datetime.combine(trading_day, US_MARKET_CLOSE, tzinfo=US_MARKET_TIMEZONE)
    return close.astimezone(timezone.utc)


def next_trading_day(day):
    """The next weekday after `day`."""
    day = day + timedelta(days=1)
    while day.weekday() >= 5:          # 5 = Saturday, 6 = Sunday
        day = day + timedelta(days=1)
    return day


def latest_close_before(now):
    """The most recent weekday whose US close is at or before `now`."""
    day = now.astimezone(US_MARKET_TIMEZONE).date()
    while day.weekday() >= 5 or us_close(day) > now:
        day = day - timedelta(days=1)
    return day


def next_check_time(now, last_data_day, fast_interval, fast_window, slow_interval):
    """
    Work out when to check next.

    now            -- current time (timezone-aware)
    last_data_day  -- the last trading day we have data for, or None
    """
    if last_data_day is None:
        return now + fast_interval

    due_day = next_trading_day(last_data_day)
    latest_close_day = latest_close_before(now)

    # The next day's data isn't due yet: sleep until its close.
    if latest_close_day < due_day:
        return us_close(due_day)

    # Data is due. Poll quickly for a while after the most recent close...
    window_start = us_close(latest_close_day)
    if now < window_start + fast_window:
        return now + fast_interval

    # ...then slowly, but be quick again from the next close.
    following_close = us_close(next_trading_day(latest_close_day))
    return min(now + slow_interval, following_close)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def show_history(watches):
    """Print the recent days of every watch with that watch's label for each day."""
    for watch in watches:
        data = fetch_indicator(watch.symbol, DAYS_TO_FETCH)
        print(f"\n{watch.symbol} ({watch.rule.describe()}) for the last {DAYS_TO_FETCH} days:")
        print("-" * 50)
        for point, label in watch.labels(data):
            print(f"{point.day}  {point.value:>10}  {label}")
        print("-" * 50)


def load_watches(args):
    """
    The watches from --watches / WATCHES. Without it, one colour watch on
    --symbol / SYMBOL at --email-frequency / EMAIL_FREQUENCY, as before v3.0.
    Raises ValueError if the setting doesn't make sense.
    """
    if args.watches:
        return parse_watches(args.watches)
    return [Watch(normalise_symbol(args.symbol), ColourRule(), parse_frequency(args.email_frequency))]


def parse_args():
    parser = argparse.ArgumentParser(description="Email (and Signal) alerts for StockCharts indicators like NYSI and NYMO.")
    parser.add_argument("--watches", default=os.getenv("WATCHES"),
                        help="What to watch, one per line or separated by ';', e.g. "
                             "'NYSI colour change; NYMO levels -50 50 change' (default: WATCHES env var; "
                             "without it, a colour watch on --symbol)")
    parser.add_argument("--symbol", default=os.getenv("SYMBOL", "NYSI"),
                        help="Without --watches: StockCharts symbol for a single colour watch "
                             "(default: NYSI, or SYMBOL env var)")
    parser.add_argument("--email-frequency", default=os.getenv("EMAIL_FREQUENCY", "change"),
                        help="Without --watches: change (only on a flip) or daily "
                             "(default: change, or EMAIL_FREQUENCY env var; colour_change and new_data still work)")
    parser.add_argument("--interval", type=int, default=int(os.getenv("CHECK_INTERVAL", "120")),
                        help="Seconds between checks while waiting for new data after the US close "
                             "(default: 120, or CHECK_INTERVAL env var)")
    parser.add_argument("--window-hours", type=float, default=float(os.getenv("FAST_WINDOW_HOURS", "4")),
                        help="How long after the US close to keep checking every --interval "
                             "(default: 4, or FAST_WINDOW_HOURS env var)")
    parser.add_argument("--slow-interval", type=int, default=int(os.getenv("SLOW_CHECK_INTERVAL", "3600")),
                        help="Seconds between checks when data is late, after the window "
                             "(default: 3600, or SLOW_CHECK_INTERVAL env var)")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR") or str(SCRIPT_DIR / "data"),
                        help="Folder for the state and history files (default: ./data, or DATA_DIR env var)")
    parser.add_argument("--signal-destination", default=os.getenv("SIGNAL_DESTINATION"),
                        help="Relay destination name for Signal messages, e.g. smtmc "
                             "(default: SIGNAL_DESTINATION env var; Signal is off without it)")
    parser.add_argument("--http-port", type=int, default=int(os.getenv("HTTP_PORT", "0")),
                        help="Serve the history read-only on this port (GET /history); 0 = off "
                             "(default: 0, or HTTP_PORT env var)")
    parser.add_argument("--check", action="store_true", help="Run one check and exit")
    parser.add_argument("--history", action="store_true", help="Show recent days for each watch and exit")
    parser.add_argument("--test-email", action="store_true", help="Send a test email and exit")
    parser.add_argument("--validate-email", action="store_true", help="Check the email login works and exit")
    parser.add_argument("--test-signal", action="store_true", help="Send a test Signal message and exit")
    parser.add_argument("--window", default=os.getenv("MONITORING_WINDOW"),
                        help="No longer used: checks are now timed from the US market close")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        watches = load_watches(args)
    except ValueError as error:
        log(f"ERROR: {error}")
        return 2
    symbols = list(dict.fromkeys(watch.symbol for watch in watches))
    data_dir = Path(args.data_dir)

    if args.history:
        show_history(watches)
        return 0

    mailer = load_mailer()
    signal = load_signal(args.signal_destination)

    if args.test_signal:
        if signal is None:
            log("ERROR: Signal isn't set up (needs signal_details.py and SIGNAL_DESTINATION)")
            return 1
        try:
            signal.send(f"🧪 Crap Stock Monitor test message (watching {', '.join(symbols)})")
            log(f"Test Signal message sent to {signal.destination}")
            return 0
        except Exception as error:
            log(f"ERROR: {error}")
            return 1

    if args.test_email or args.validate_email:
        if mailer is None:
            log("ERROR: email_details.py not found next to the script")
            return 1
        try:
            if args.test_email:
                mailer.send("Crap Stock Monitor test", "<p>Test email from Crap Stock Monitor.</p>")
                log(f"Test email sent to {', '.join(mailer.recipients)}")
            else:
                mailer.validate()
                log("Email login works")
            return 0
        except Exception as error:
            log(f"ERROR: {error}")
            return 1

    if args.window:
        log("NOTE: --window / MONITORING_WINDOW is no longer used and is ignored")
    if os.getenv("SIGNAL_FREQUENCY"):
        log("NOTE: SIGNAL_FREQUENCY is no longer used; each watch's frequency applies to email and Signal")

    if mailer is None:
        log("WARNING: no email_details.py found, so no emails will be sent")
    else:
        try:
            mailer.validate()
            log(f"Email login works. Alerts go to: {', '.join(mailer.recipients)}")
        except Exception as error:
            log(f"WARNING: email login failed ({error}). Will keep trying when an email is due.")

    if signal is not None:
        log(f"Signal on: posting to {signal.destination}")
    for watch in watches:
        log(f"Watching {watch.describe()}")

    if args.check:
        run_once(watches, data_dir, mailer, signal=signal, keep_history=True)
        return 0

    fast_interval = timedelta(seconds=args.interval)
    fast_window = timedelta(hours=args.window_hours)
    slow_interval = timedelta(seconds=args.slow_interval)
    if args.http_port:
        start_http_server(args.http_port, data_dir, symbols)

    log(f"Checking every {args.interval}s for {args.window_hours}h after each US close, "
        f"then every {args.slow_interval}s if data is late. State: {data_dir}")
    try:
        while True:
            try:
                run_once(watches, data_dir, mailer, signal=signal, keep_history=True)
            except Exception as error:
                log(f"ERROR: unexpected problem during check: {error}")

            last_data_day = oldest_data_day(watches, data_dir)
            now = datetime.now(timezone.utc)
            wake_at = next_check_time(now, last_data_day, fast_interval, fast_window, slow_interval)
            wait_seconds = max(1, (wake_at - now).total_seconds())
            if wait_seconds > 600:
                log(f"Next check at {wake_at.astimezone().strftime('%a %Y-%m-%d %H:%M %Z')}")
            time.sleep(wait_seconds)
    except KeyboardInterrupt:
        log("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

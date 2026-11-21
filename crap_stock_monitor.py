#!/usr/bin/env python3
"""
Crap Stock Monitor

Watches the NYSI (NYSE McClellan Summation Index) on StockCharts and emails you
when it flips between Red (falling) and Black (rising).

How it works:
  - Every --interval seconds it downloads the last few weeks of NYSI values.
  - When a new trading day appears, it works out that day's colour.
  - If the colour differs from the last one it saw, it sends an email.
  - It remembers the last day and colour in a small state file, so a restart
    doesn't send duplicate alerts.
  - If no new data turns up for several days (StockCharts down, endpoint
    changed, etc.) it sends one warning email so a silent failure gets noticed.
"""

import argparse
import importlib.util
import json
import math
import os
import smtplib
import sys
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests


SCRIPT_DIR = Path(__file__).resolve().parent

STOCKCHARTS_URL = "https://stockcharts.com/c-sc/sc"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
DAYS_TO_FETCH = 20            # calendar days; plenty to find the last real move
HTTP_TIMEOUT_SECONDS = 15

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 30

# US data for Friday arrives on Saturday morning in Australia, so a normal
# weekend means 3 days without new data, a long weekend 4-5. Six or more
# days means something is wrong.
STALE_AFTER_DAYS = 5

EMAIL_FREQUENCIES = ["colour_change", "daily"]
BLACK = "Black"   # NYSI went up
RED = "Red"       # NYSI went down


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


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

@dataclass
class State:
    last_date: str = None         # 'YYYY-MM-DD' of the last trading day processed
    last_colour: str = None
    last_value: float = None
    stale_alert_sent: bool = False


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

    # 'last_color' is the spelling used by v2.5 and earlier.
    return State(
        last_date=saved.get("last_date"),
        last_colour=saved.get("last_colour") or saved.get("last_color"),
        last_value=saved.get("last_value"),
        stale_alert_sent=saved.get("stale_alert_sent", False),
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


def build_email(kind, symbol, point=None, old_colour=None, new_colour=None, last_seen=None):
    """Return (subject, html) for an email. `kind` is 'colour_change', 'daily' or 'stale'."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if kind == "stale":
        subject = f"{symbol} Monitor: No new data since {last_seen}"
        body = (
            f"<p>The monitor has not seen any new {symbol} data since <strong>{last_seen}</strong>.</p>"
            f"<p>StockCharts may be down, or the data endpoint may have changed. "
            f"Check the container logs.</p>"
        )
    else:
        changed = old_colour != new_colour
        if kind == "colour_change":
            subject = f"{symbol} Alert: colour changed from {old_colour} to {new_colour}"
        elif changed:
            subject = f"{symbol} Daily Update: {new_colour} (changed from {old_colour} to {new_colour})"
        else:
            subject = f"{symbol} Daily Update: {new_colour} (no change)"

        if changed:
            colour_line = f"<p><strong>Change:</strong> {old_colour} &rarr; {new_colour}</p>"
        else:
            colour_line = f"<p><strong>Colour:</strong> {new_colour} (unchanged)</p>"

        body = (
            f"{colour_line}"
            f"<p><strong>{symbol} value:</strong> {point.value}</p>"
            f"<p><strong>Trading day:</strong> {point.day.isoformat()}</p>"
        )

    html = (
        f"<html><body>"
        f"<h2>{subject}</h2>"
        f"{body}"
        f"<p><small>Sent {now} by Crap Stock Monitor.</small></p>"
        f"</body></html>"
    )
    return subject, html


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


def check_for_stale_data(state, last_data_day, mailer, symbol, today):
    """Send one warning when data stops arriving. Returns True if the state changed."""
    if last_data_day is None or state.stale_alert_sent:
        return False
    if not is_stale(last_data_day, today):
        return False

    subject, html = build_email("stale", symbol, last_seen=last_data_day.isoformat())
    if try_send(mailer, subject, html):
        state.stale_alert_sent = True
        return True
    return False


def run_once(state_path, symbol, email_frequency, mailer, fetch=fetch_indicator, today=None):
    """
    Do one check: fetch data, and if there's a new trading day, update the
    state and send whatever email is due. The state only moves forward once
    the email has been sent, so a failed email is retried on the next check.
    """
    today = today or date.today()
    symbol = normalise_symbol(symbol)
    state = load_state(state_path)
    saved_day = date.fromisoformat(state.last_date) if state.last_date else None

    try:
        data = fetch(symbol, DAYS_TO_FETCH)
    except Exception as error:
        log(f"ERROR: could not fetch {symbol} data: {error}")
        if check_for_stale_data(state, saved_day, mailer, symbol, today):
            save_state(state_path, state)
        return

    colour = latest_colour(data)
    if colour is None:
        log(f"WARNING: not enough {symbol} data to work out a colour ({len(data)} rows)")
        return

    latest = data[-1]

    # First ever run: remember where we are, don't email.
    if state.last_date is None:
        log(f"First run: {symbol} {latest.value} on {latest.day} is {colour}. Saved as the starting point.")
        save_state(state_path, State(latest.day.isoformat(), colour, latest.value))
        return

    # Nothing new yet.
    if latest.day <= saved_day:
        log(f"No new data yet (latest is {latest.day}, {state.last_colour})")
        if check_for_stale_data(state, latest.day, mailer, symbol, today):
            save_state(state_path, state)
        return

    # A new trading day.
    old_colour = state.last_colour
    changed = colour != old_colour
    log(f"New data: {symbol} {latest.value} on {latest.day} is {colour} (was {old_colour})")

    email_due = changed or email_frequency == "daily"
    if email_due:
        if changed and email_frequency == "colour_change":
            kind = "colour_change"
        else:
            kind = "daily"
        subject, html = build_email(kind, symbol, latest, old_colour, colour)
        if not try_send(mailer, subject, html):
            log("State not updated, so the email will be retried on the next check")
            return

    save_state(state_path, State(latest.day.isoformat(), colour, latest.value, stale_alert_sent=False))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def show_history(symbol):
    data = fetch_indicator(symbol, DAYS_TO_FETCH)
    print(f"\n{symbol} colours for the last {DAYS_TO_FETCH} days:")
    print("-" * 40)
    for point, colour in colour_history(data):
        print(f"{point.day}  {point.value:>10}  {colour}")
    print("-" * 40)


def parse_args():
    parser = argparse.ArgumentParser(description="Email an alert when the NYSI flips between Red and Black.")
    parser.add_argument("--symbol", default=os.getenv("SYMBOL", "NYSI"),
                        help="StockCharts symbol, with or without '$' (default: NYSI, or SYMBOL env var)")
    parser.add_argument("--interval", type=int, default=int(os.getenv("CHECK_INTERVAL", "900")),
                        help="Seconds between checks (default: 900, or CHECK_INTERVAL env var)")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR") or str(SCRIPT_DIR / "data"),
                        help="Folder for the state file (default: ./data, or DATA_DIR env var)")
    parser.add_argument("--email-frequency", default=os.getenv("EMAIL_FREQUENCY", "colour_change"),
                        choices=EMAIL_FREQUENCIES + ["new_data"],
                        help="colour_change: only email on a flip. daily: email every new trading day. "
                             "(default: colour_change, or EMAIL_FREQUENCY env var; new_data is an old name for daily)")
    parser.add_argument("--check", action="store_true", help="Run one check and exit")
    parser.add_argument("--history", action="store_true", help="Show recent colours and exit")
    parser.add_argument("--test-email", action="store_true", help="Send a test email and exit")
    parser.add_argument("--validate-email", action="store_true", help="Check the email login works and exit")
    parser.add_argument("--window", default=os.getenv("MONITORING_WINDOW"),
                        help="No longer used: the monitor now checks all day and only acts on new data")
    return parser.parse_args()


def main():
    args = parse_args()
    symbol = normalise_symbol(args.symbol)
    email_frequency = "daily" if args.email_frequency == "new_data" else args.email_frequency
    state_path = Path(args.data_dir) / f"{symbol}_state.json"

    if args.history:
        show_history(symbol)
        return 0

    mailer = load_mailer()

    if args.test_email or args.validate_email:
        if mailer is None:
            log("ERROR: email_details.py not found next to the script")
            return 1
        try:
            if args.test_email:
                mailer.send(f"{symbol} Monitor test", "<p>Test email from Crap Stock Monitor.</p>")
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

    if mailer is None:
        log("WARNING: no email_details.py found, so no emails will be sent")
    else:
        try:
            mailer.validate()
            log(f"Email login works. Alerts go to: {', '.join(mailer.recipients)}")
        except Exception as error:
            log(f"WARNING: email login failed ({error}). Will keep trying when an email is due.")

    if args.check:
        run_once(state_path, symbol, email_frequency, mailer)
        return 0

    log(f"Watching {symbol} every {args.interval}s, emailing on: {email_frequency}. State: {state_path}")
    try:
        while True:
            try:
                run_once(state_path, symbol, email_frequency, mailer)
            except Exception as error:
                log(f"ERROR: unexpected problem during check: {error}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

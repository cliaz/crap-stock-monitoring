"""
Tests for crap_stock_monitor.py

Run them with:
    pip install -r requirements-dev.txt
    python -m pytest
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import crap_stock_monitor as csm
from crap_stock_monitor import DataPoint, State


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def points(*pairs):
    """Build a list of DataPoints from ('YYYY-MM-DD', value) pairs."""
    return [DataPoint(date.fromisoformat(day), value) for day, value in pairs]


def stockcharts_text(*pairs):
    """Build a fake StockCharts response in the same format as the real one."""
    rows = []
    for index, (day, value) in enumerate(pairs):
        compact = day.replace('-', '')
        rows.append(f"{index} {compact}0930 {compact}1600 {value} 0")
    return "<html><pricedata>" + "|".join(rows) + "</pricedata></html>"


class FakeMailer:
    """Records emails instead of sending them. Can be told to fail."""

    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, subject, html):
        if self.fail:
            raise OSError("SMTP is down")
        self.sent.append(subject)


class FakeFetcher:
    """Returns pre-set data, or raises to simulate a network failure."""

    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error
        self.calls = 0

    def __call__(self, symbol, days):
        self.calls += 1
        if self.error:
            raise self.error
        return self.data


# ---------------------------------------------------------------------------
# Symbol handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["NYSI", "$NYSI", " nysi ", "$nysi"])
def test_normalise_symbol_strips_dollar_and_spaces(raw):
    assert csm.normalise_symbol(raw) == "NYSI"


def test_stockcharts_url_has_exactly_one_dollar_sign():
    url = csm.build_stockcharts_url("$NYSI", days=15)
    assert "s=$NYSI&" in url
    assert "$$" not in url


# ---------------------------------------------------------------------------
# Parsing the StockCharts response
# ---------------------------------------------------------------------------

def test_parse_reads_dates_and_values_in_date_order():
    text = stockcharts_text(("2026-11-19", -463.11), ("2026-11-18", -392.2))
    result = csm.parse_stockcharts(text)
    assert result == points(("2026-11-18", -392.2), ("2026-11-19", -463.11))


def test_parse_skips_nan_and_malformed_rows():
    text = "<pricedata>0 202611180930 202611181600 nan 0|garbage|1 202611190930 202611191600 5.5 0</pricedata>"
    assert csm.parse_stockcharts(text) == points(("2026-11-19", 5.5))


def test_parse_raises_when_pricedata_block_is_missing():
    with pytest.raises(ValueError):
        csm.parse_stockcharts("<html>Access denied</html>")


# ---------------------------------------------------------------------------
# Working out the colour
# ---------------------------------------------------------------------------

def test_rising_value_is_black():
    assert csm.latest_colour(points(("2026-11-18", 1.0), ("2026-11-19", 2.0))) == "Black"


def test_falling_value_is_red():
    assert csm.latest_colour(points(("2026-11-18", 2.0), ("2026-11-19", 1.0))) == "Red"


def test_flat_day_keeps_the_previous_direction():
    data = points(("2026-11-17", 1.0), ("2026-11-18", 2.0), ("2026-11-19", 2.0))
    assert csm.latest_colour(data) == "Black"


def test_flat_with_no_earlier_move_defaults_to_red():
    data = points(("2026-11-18", 2.0), ("2026-11-19", 2.0))
    assert csm.latest_colour(data) == "Red"


def test_colour_needs_at_least_two_points():
    assert csm.latest_colour(points(("2026-11-19", 2.0))) is None


def test_colour_history_matches_latest_colour_for_each_day():
    data = points(("2026-11-16", 1.0), ("2026-11-17", 3.0), ("2026-11-18", 3.0), ("2026-11-19", 0.5))
    history = csm.colour_history(data)
    assert [colour for _, colour in history] == ["Black", "Black", "Red"]


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def test_missing_state_file_gives_empty_state(tmp_path):
    state = csm.load_state(tmp_path / "NYSI_state.json")
    assert state == State()


def test_state_round_trip(tmp_path):
    path = tmp_path / "NYSI_state.json"
    original = State(last_date="2026-11-19", last_label="Red", last_value=-463.11, stale_alert_sent=True)
    csm.save_state(path, original)
    assert csm.load_state(path) == original


def test_old_v25_state_file_is_still_understood(tmp_path):
    path = tmp_path / "NYSI_state.json"
    old_format = {
        "history": [
            {"color": "Black", "date": "2025-12-18", "value": 10.0},
            {"color": "Red", "date": "2025-12-19", "value": 9.0},
        ],
        "last_color": "Red",
        "last_date": "2025-12-19",
        "last_value": 9.0,
    }
    path.write_text(json.dumps(old_format))
    assert csm.load_state(path) == State(last_date="2025-12-19", last_label="Red", last_value=9.0)


def test_corrupt_state_file_gives_empty_state(tmp_path):
    path = tmp_path / "NYSI_state.json"
    path.write_text("{not json")
    assert csm.load_state(path) == State()


# ---------------------------------------------------------------------------
# One check (run_once)
# ---------------------------------------------------------------------------

FRIDAY_DATA = points(("2026-11-18", 1.0), ("2026-11-19", 2.0), ("2026-11-20", 3.0))   # Black
FLIP_TO_RED = points(("2026-11-19", 2.0), ("2026-11-20", 3.0), ("2026-11-23", 1.0))   # Red
STAYS_BLACK = points(("2026-11-19", 2.0), ("2026-11-20", 3.0), ("2026-11-23", 4.0))   # Black
SATURDAY = date(2026, 11, 21)
TUESDAY = date(2026, 11, 24)


def nysi_colour(frequency="change"):
    return csm.Watch("NYSI", csm.ColourRule(), csm.parse_frequency(frequency))


def run(tmp_path, data, mailer, frequency="change", today=SATURDAY, error=None):
    return csm.run_once([nysi_colour(frequency)], tmp_path, mailer, fetch=FakeFetcher(data, error), today=today)


def test_first_run_saves_state_without_emailing(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer)
    assert mailer.sent == []
    state = csm.load_state(tmp_path / "NYSI_state.json")
    assert state.last_date == "2026-11-20"
    assert state.last_label == "Black"


def test_flip_sends_exactly_one_email(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer)
    run(tmp_path, FLIP_TO_RED, mailer, today=TUESDAY)
    run(tmp_path, FLIP_TO_RED, mailer, today=TUESDAY)   # same data polled again
    assert len(mailer.sent) == 1
    assert mailer.sent[0] == "NYSI Alert: Black → Red, 1.00 (-2.00)"


def test_no_flip_sends_nothing_in_change_mode(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer)
    run(tmp_path, STAYS_BLACK, mailer, today=TUESDAY)
    assert mailer.sent == []
    assert csm.load_state(tmp_path / "NYSI_state.json").last_date == "2026-11-23"


def test_daily_mode_emails_once_per_new_trading_day(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer, frequency="daily")
    run(tmp_path, STAYS_BLACK, mailer, frequency="daily", today=TUESDAY)
    run(tmp_path, STAYS_BLACK, mailer, frequency="daily", today=TUESDAY)
    assert len(mailer.sent) == 1
    assert mailer.sent[0] == "NYSI Daily Update: Black, 4.00 (+1.00)"


def test_daily_mode_says_when_it_flipped(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer, frequency="daily")
    run(tmp_path, FLIP_TO_RED, mailer, frequency="daily", today=TUESDAY)
    assert mailer.sent[0] == "NYSI Daily Update: Black → Red, 1.00 (-2.00)"


def test_failed_email_is_retried_on_the_next_check(tmp_path):
    run(tmp_path, FRIDAY_DATA, FakeMailer())
    broken = FakeMailer(fail=True)
    run(tmp_path, FLIP_TO_RED, broken, today=TUESDAY)
    assert csm.load_state(tmp_path / "NYSI_state.json").last_label == "Black"   # not advanced

    working = FakeMailer()
    run(tmp_path, FLIP_TO_RED, working, today=TUESDAY)
    assert len(working.sent) == 1
    assert csm.load_state(tmp_path / "NYSI_state.json").last_label == "Red"


def test_without_email_config_state_still_advances(tmp_path):
    run(tmp_path, FRIDAY_DATA, None)
    run(tmp_path, FLIP_TO_RED, None, today=TUESDAY)
    assert csm.load_state(tmp_path / "NYSI_state.json").last_label == "Red"


def test_fetch_failure_does_not_crash_or_touch_state(tmp_path):
    run(tmp_path, FRIDAY_DATA, FakeMailer())
    run(tmp_path, None, FakeMailer(), today=TUESDAY, error=OSError("network down"))
    assert csm.load_state(tmp_path / "NYSI_state.json").last_date == "2026-11-20"


def test_check_fetches_data_only_once(tmp_path):
    fetcher = FakeFetcher(FRIDAY_DATA)
    csm.run_once([nysi_colour()], tmp_path, None, fetch=fetcher, today=SATURDAY)
    assert fetcher.calls == 1


# ---------------------------------------------------------------------------
# Stale data warning
# ---------------------------------------------------------------------------

def test_long_weekend_is_not_stale():
    assert not csm.is_stale(date(2026, 11, 20), today=date(2026, 11, 25))   # 5 days


def test_six_days_without_new_data_is_stale():
    assert csm.is_stale(date(2026, 11, 20), today=date(2026, 11, 26))


def test_stale_warning_is_sent_once_and_rearms_when_data_returns(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer)
    a_week_later = date(2026, 11, 27)
    run(tmp_path, FRIDAY_DATA, mailer, today=a_week_later)
    run(tmp_path, FRIDAY_DATA, mailer, today=a_week_later)
    assert len(mailer.sent) == 1
    assert "No new" in mailer.sent[0]

    fresh = points(("2026-11-25", 3.0), ("2026-11-26", 4.0))
    run(tmp_path, fresh, mailer, today=a_week_later)
    assert csm.load_state(tmp_path / "NYSI_state.json").stale_alert_sent is False


def test_fetch_failures_also_trigger_the_stale_warning(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer)
    run(tmp_path, None, mailer, today=date(2026, 11, 27), error=OSError("network down"))
    assert len(mailer.sent) == 1


# ---------------------------------------------------------------------------
# Replay: a run of real-looking days with several flips
# ---------------------------------------------------------------------------

def test_replay_emails_once_per_flip(tmp_path):
    values = [10, 12, 15, 14, 13, 13, 16, 18, 17, 19]   # B B R R R(flat) B B R B
    days = [
        "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
        "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
    ]
    series = points(*zip(days, values))
    mailer = FakeMailer()
    for end in range(2, len(series) + 1):
        visible = series[:end]
        today = visible[-1].day
        run(tmp_path, visible, mailer, today=today)
        run(tmp_path, visible, mailer, today=today)   # every day is polled more than once
    assert mailer.sent == [
        "NYSI Alert: Black → Red, 14.00 (-1.00)",
        "NYSI Alert: Red → Black, 16.00 (+3.00)",
        "NYSI Alert: Black → Red, 17.00 (-1.00)",
        "NYSI Alert: Red → Black, 19.00 (+2.00)",
    ]


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def test_mailer_connects_with_a_timeout(monkeypatch):
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            seen["timeout"] = timeout
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def starttls(self):
            pass
        def login(self, user, password):
            pass
        def send_message(self, msg):
            seen["to"] = msg["To"]

    monkeypatch.setattr(csm.smtplib, "SMTP", FakeSMTP)
    mailer = csm.Mailer("me@example.com", "secret", ["a@example.com", "b@example.com"])
    mailer.send("subject", "<p>body</p>")
    assert seen["timeout"] == csm.SMTP_TIMEOUT_SECONDS
    assert seen["to"] == "a@example.com, b@example.com"


def test_email_details_are_loaded_from_the_script_folder_not_the_cwd(tmp_path, monkeypatch):
    config = tmp_path / "email_details.py"
    config.write_text('sender_email = "me@example.com"\nsender_password_password = "pw"\nrecipients = []\n')
    monkeypatch.chdir("/")
    mailer = csm.load_mailer(config)
    assert mailer.sender == "me@example.com"
    assert mailer.recipients == ["me@example.com"]   # falls back to the sender


def test_missing_email_details_gives_no_mailer(tmp_path):
    assert csm.load_mailer(tmp_path / "email_details.py") is None


# ---------------------------------------------------------------------------
# Scheduling: when to check next
# ---------------------------------------------------------------------------
#
# The new day's data appears soon after the US close (16:00 New York time).
# The monitor polls quickly for FAST_WINDOW after that, then sleeps until the
# next trading day's close. If the data is late (US holiday, StockCharts slow)
# it drops to a slow poll, but is quick again at the next close.

FAST = timedelta(minutes=2)
WINDOW = timedelta(hours=3)
SLOW = timedelta(hours=1)


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def next_check(now, last_data_day):
    return csm.next_check_time(now, last_data_day, fast_interval=FAST, fast_window=WINDOW, slow_interval=SLOW)


def test_us_close_in_utc_follows_new_york_daylight_saving():
    assert csm.us_close(date(2026, 11, 23)) == utc("2026-11-23 21:00")   # EST
    assert csm.us_close(date(2026, 7, 20)) == utc("2026-07-20 20:00")    # EDT


def test_next_trading_day_skips_the_weekend():
    assert csm.next_trading_day(date(2026, 11, 20)) == date(2026, 11, 23)   # Fri -> Mon
    assert csm.next_trading_day(date(2026, 11, 23)) == date(2026, 11, 24)   # Mon -> Tue


def test_after_fridays_data_it_sleeps_until_mondays_us_close():
    saturday_morning_melbourne = utc("2026-11-20 22:30")
    assert next_check(saturday_morning_melbourne, date(2026, 11, 20)) == utc("2026-11-23 21:00")   # Tue 08:00 AEDT


def test_polls_fast_inside_the_window():
    now = utc("2026-11-23 21:30")
    assert next_check(now, date(2026, 11, 20)) == now + FAST


def test_polls_slowly_once_the_window_has_passed_without_data():
    now = utc("2026-11-24 01:00")   # 4 hours after the close
    assert next_check(now, date(2026, 11, 20)) == now + SLOW


def test_slow_polling_is_cut_short_for_the_next_close():
    now = utc("2026-11-24 20:50")   # data for Monday never came; Tuesday closes at 21:00
    assert next_check(now, date(2026, 11, 20)) == utc("2026-11-24 21:00")


def test_after_a_us_holiday_it_polls_fast_at_the_next_close():
    # Thanksgiving: no data for Thu 26 Nov. Friday's close is 2026-11-27 21:00 UTC.
    now = utc("2026-11-27 21:10")
    assert next_check(now, date(2026, 11, 25)) == now + FAST


def test_melbourne_mid_year_release_is_six_am():
    # Mid-year (EDT and AEST) the close is 20:00 UTC = 06:00 in Melbourne.
    friday_night = utc("2026-07-17 22:00")
    assert next_check(friday_night, date(2026, 7, 17)) == utc("2026-07-20 20:00")


def test_daylight_saving_changeover_weeks():
    # March 2027: the US is on EDT, Melbourne still on AEDT, so the close is 07:00 in Melbourne.
    assert csm.us_close(date(2027, 3, 15)) == utc("2027-03-15 20:00")
    # October 2026: Melbourne is on AEDT, the US still on EDT, again 07:00 in Melbourne.
    assert csm.us_close(date(2026, 10, 12)) == utc("2026-10-12 20:00")


def test_no_saved_data_yet_retries_after_the_fast_interval():
    now = utc("2026-11-22 03:00")
    assert next_check(now, None) == now + FAST


def test_very_old_state_counts_as_overdue_data():
    inside_a_window = utc("2026-11-23 21:30")
    outside_a_window = utc("2026-11-23 10:00")
    assert next_check(inside_a_window, date(2025, 12, 19)) == inside_a_window + FAST
    assert next_check(outside_a_window, date(2025, 12, 19)) == outside_a_window + SLOW


def test_never_schedules_in_the_past():
    now = utc("2026-11-23 21:00")   # exactly at the close
    assert next_check(now, date(2026, 11, 20)) > now



# ---------------------------------------------------------------------------
# Daily change and streak
# ---------------------------------------------------------------------------

WEEK = points(
    ("2026-11-10", 100.0),
    ("2026-11-11", 110.0),   # Black
    ("2026-11-12", 105.0),   # Red
    ("2026-11-13", 101.5),   # Red
    ("2026-11-16", 101.5),   # flat, stays Red
    ("2026-11-17", 90.25),   # Red
)


def test_daily_change_is_latest_minus_previous_trading_day():
    assert csm.daily_change(WEEK) == pytest.approx(90.25 - 101.5)


def test_daily_change_on_a_flat_day_is_zero():
    assert csm.daily_change(WEEK[:5]) == 0


def test_daily_change_needs_two_days():
    assert csm.daily_change(WEEK[:1]) is None


def test_streak_counts_consecutive_days_of_the_latest_colour():
    # 12, 13, 16 (flat, carried Red) and 17 Nov are all Red.
    assert csm.colour_streak(WEEK) == (4, True)


def test_streak_is_open_ended_when_it_fills_the_whole_download():
    rising = points(("2026-11-16", 1.0), ("2026-11-17", 2.0), ("2026-11-18", 3.0))
    assert csm.colour_streak(rising) == (2, False)   # shown as "2+ days"


def test_previous_streak_is_the_run_before_a_flip():
    flip = WEEK[:3]   # Black on the 11th, then Red on the 12th
    assert csm.colour_streak(flip[:-1]) == (1, False)


def build(kind, data, old_colour, new_colour):
    return csm.build_email(kind, nysi_colour(), data=data, old_label=old_colour, new_label=new_colour)


def test_daily_email_shows_value_change_and_streak():
    subject, html = build("daily", WEEK, "Red", "Red")
    assert subject == "NYSI Daily Update: Red, 90.25 (-11.25)"
    assert "90.25" in html
    assert "-11.25 since 2026-11-16" in html
    assert "Red for 4 days in a row" in html


def test_flip_email_says_how_long_the_previous_run_was():
    data = WEEK[:3]
    subject, html = build("change", data, "Black", "Red")
    assert subject == "NYSI Alert: Black → Red, 105.00 (-5.00)"
    assert "Black → Red, after" in html
    assert "after 1+ day Black" in html


def test_flat_day_email_says_the_colour_stays():
    subject, html = build("daily", WEEK[:5], "Red", "Red")
    assert subject == "NYSI Daily Update: Red, 101.50 (0.00)"
    assert "0.00 since 2026-11-13 (flat, stays Red)" in html


def test_open_ended_streak_is_shown_with_a_plus():
    rising = points(("2026-11-16", 1.0), ("2026-11-17", 2.0), ("2026-11-18", 3.0))
    _, html = build("daily", rising, "Black", "Black")
    assert "Black for 2+ days in a row" in html


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

class FakeSignal:
    """Records Signal messages instead of posting them. Can be told to fail."""

    def __init__(self, fail=False):
        self.destination = "smtmc"
        self.fail = fail
        self.sent = []

    def send(self, text):
        if self.fail:
            raise OSError("relay is down")
        self.sent.append(text)


def run_with_signal(tmp_path, data, mailer, signal, frequency="change", today=SATURDAY):
    return csm.run_once([nysi_colour(frequency)], tmp_path, mailer, fetch=FakeFetcher(data), today=today,
                        signal=signal)


def test_signal_message_on_a_flip():
    text = csm.build_signal_message(nysi_colour(), FLIP_TO_RED, "Black", "Red")
    assert text == "🔴 NYSI Black → Red: 1.00 (-2.00) on 2026-11-23. Black lasted 1+ day."


def test_signal_message_without_a_flip():
    text = csm.build_signal_message(nysi_colour(), STAYS_BLACK, "Black", "Black")
    assert text == "⚫ NYSI 4.00 (+1.00) on 2026-11-23, Black for 2+ days in a row."


def test_flip_posts_one_signal_message(tmp_path):
    signal = FakeSignal()
    run_with_signal(tmp_path, FRIDAY_DATA, FakeMailer(), signal)
    run_with_signal(tmp_path, FLIP_TO_RED, FakeMailer(), signal, today=TUESDAY)
    run_with_signal(tmp_path, FLIP_TO_RED, FakeMailer(), signal, today=TUESDAY)
    assert len(signal.sent) == 1
    assert signal.sent[0].startswith("🔴 NYSI Black → Red")


def test_change_watch_posts_nothing_on_a_day_without_a_flip(tmp_path):
    mailer, signal = FakeMailer(), FakeSignal()
    run_with_signal(tmp_path, FRIDAY_DATA, mailer, signal)
    run_with_signal(tmp_path, STAYS_BLACK, mailer, signal, today=TUESDAY)
    assert mailer.sent == [] and signal.sent == []


def test_daily_watch_emails_and_posts_every_new_trading_day(tmp_path):
    mailer, signal = FakeMailer(), FakeSignal()
    run_with_signal(tmp_path, FRIDAY_DATA, mailer, signal, frequency="daily")
    run_with_signal(tmp_path, STAYS_BLACK, mailer, signal, frequency="daily", today=TUESDAY)
    assert len(mailer.sent) == 1 and len(signal.sent) == 1


def test_signal_failure_does_not_hold_up_the_state(tmp_path):
    mailer = FakeMailer()
    run_with_signal(tmp_path, FRIDAY_DATA, mailer, FakeSignal(fail=True))
    run_with_signal(tmp_path, FLIP_TO_RED, mailer, FakeSignal(fail=True), today=TUESDAY)
    assert len(mailer.sent) == 1
    assert csm.load_state(tmp_path / "NYSI_state.json").last_label == "Red"


def test_signal_waits_for_the_email_so_a_retry_does_not_post_twice(tmp_path):
    signal = FakeSignal()
    run_with_signal(tmp_path, FRIDAY_DATA, FakeMailer(), signal)
    run_with_signal(tmp_path, FLIP_TO_RED, FakeMailer(fail=True), signal, today=TUESDAY)
    assert signal.sent == []
    run_with_signal(tmp_path, FLIP_TO_RED, FakeMailer(), signal, today=TUESDAY)
    assert len(signal.sent) == 1


def test_signature_matches_the_relay():
    # Same inputs as the Node relay's HMAC; the expected value was produced by Node.
    relay = csm.SignalRelay("http://relay", "crap-stock-monitor", "s3cret", "task-mgmt")
    headers = relay.signed_headers(b'{"to":"task-mgmt","message":"hi"}', timestamp="1700000000", nonce="abc123")
    assert headers["X-Relay-Signature"] == "8004a4326fcfa9d984be537beb946a4174dbf5791ab4f97756f901e02aab3357"
    assert headers["X-Relay-Client"] == "crap-stock-monitor"


def test_signal_send_uses_a_timeout_and_raises_on_rejection(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 403
        text = '{"error":"destination not allowed"}'

    def fake_post(url, data, headers, timeout):
        calls.append((url, json.loads(data), timeout))
        return FakeResponse()

    monkeypatch.setattr(csm.requests, "post", fake_post)
    relay = csm.SignalRelay("http://relay/", "crap-stock-monitor", "s3cret", "smtmc")
    with pytest.raises(RuntimeError, match="403"):
        relay.send("hello")
    assert calls == [("http://relay/send", {"to": "smtmc", "message": "hello"}, 10)]


def test_signal_is_off_without_details_or_destination(tmp_path):
    assert csm.load_signal("task-mgmt", tmp_path / "missing.py") is None
    details = tmp_path / "signal_details.py"
    details.write_text('relay_url = "http://relay"\nclient_name = "c"\nsecret = "s"\n')
    assert csm.load_signal(None, details) is None
    relay = csm.load_signal("smtmc", details)
    assert (relay.destination, relay.client) == ("smtmc", "c")


# ---------------------------------------------------------------------------
# History file and report
# ---------------------------------------------------------------------------

class DaysFetcher:
    """Returns different data depending on how many days were asked for, and records the requests."""

    def __init__(self, recent, full=None, full_error=None):
        self.recent = recent
        self.full = full
        self.full_error = full_error
        self.requests = []

    def __call__(self, symbol, days):
        self.requests.append(days)
        if days == csm.HISTORY_FULL_DAYS:
            if self.full_error:
                raise self.full_error
            return self.full
        return self.recent


LONG_HISTORY = points(
    ("2026-11-02", 10.0), ("2026-11-03", 11.0), ("2026-11-04", 12.0),   # Black, Black
    ("2026-11-05", 11.0), ("2026-11-06", 10.0), ("2026-11-09", 9.0),    # Red x3
    ("2026-11-10", 9.0),                                                # flat, stays Red
    ("2026-11-11", 9.5),                                                # Black
)


def test_history_round_trip(tmp_path):
    path = tmp_path / "NYSI_history.csv"
    csm.save_history(path, FRIDAY_DATA, date(2026, 11, 21))
    loaded, full_day = csm.load_history(path)
    assert loaded == FRIDAY_DATA
    assert full_day == date(2026, 11, 21)


def test_missing_history_file_is_empty(tmp_path):
    assert csm.load_history(tmp_path / "missing.csv") == ([], None)


def test_bad_history_rows_are_skipped(tmp_path):
    path = tmp_path / "NYSI_history.csv"
    path.write_text("# full download: 2026-11-21\ndate,value\n2026-11-19,2.0\nrubbish\n2026-11-20,oops\n2026-11-18,1.0\n")
    loaded, _ = csm.load_history(path)
    assert loaded == points(("2026-11-18", 1.0), ("2026-11-19", 2.0))


def test_merge_prefers_the_newer_download():
    old = points(("2026-11-19", 2.0), ("2026-11-20", 3.0))
    new = points(("2026-11-20", 3.5), ("2026-11-23", 1.0))
    assert csm.merge_points(old, new) == points(("2026-11-19", 2.0), ("2026-11-20", 3.5), ("2026-11-23", 1.0))


def test_first_update_downloads_the_full_history(tmp_path):
    path = tmp_path / "NYSI_history.csv"
    fetcher = DaysFetcher(recent=FRIDAY_DATA, full=LONG_HISTORY)
    csm.update_history(path, "NYSI", FRIDAY_DATA, fetcher, SATURDAY)
    assert fetcher.requests == [csm.HISTORY_FULL_DAYS]
    loaded, full_day = csm.load_history(path)
    assert loaded == csm.merge_points(LONG_HISTORY, FRIDAY_DATA)
    assert full_day == SATURDAY


def test_later_updates_only_merge_until_a_month_has_passed(tmp_path):
    path = tmp_path / "NYSI_history.csv"
    fetcher = DaysFetcher(recent=FRIDAY_DATA, full=LONG_HISTORY)
    csm.update_history(path, "NYSI", FRIDAY_DATA, fetcher, SATURDAY)
    csm.update_history(path, "NYSI", FLIP_TO_RED, fetcher, TUESDAY)
    assert fetcher.requests == [csm.HISTORY_FULL_DAYS]
    assert csm.load_history(path)[0][-1] == DataPoint(date(2026, 11, 23), 1.0)

    csm.update_history(path, "NYSI", FLIP_TO_RED, fetcher, SATURDAY + timedelta(days=30))
    assert fetcher.requests == [csm.HISTORY_FULL_DAYS, csm.HISTORY_FULL_DAYS]


def test_a_failed_full_download_still_keeps_the_recent_data(tmp_path):
    path = tmp_path / "NYSI_history.csv"
    fetcher = DaysFetcher(recent=FRIDAY_DATA, full_error=OSError("too big"))
    csm.update_history(path, "NYSI", FRIDAY_DATA, fetcher, SATURDAY)
    assert csm.load_history(path)[0] == FRIDAY_DATA


def test_unchanged_history_is_not_rewritten(tmp_path, monkeypatch):
    path = tmp_path / "NYSI_history.csv"
    fetcher = DaysFetcher(recent=FRIDAY_DATA, full=FRIDAY_DATA)
    csm.update_history(path, "NYSI", FRIDAY_DATA, fetcher, SATURDAY)
    writes = []
    monkeypatch.setattr(csm, "save_history", lambda *args: writes.append(args))
    csm.update_history(path, "NYSI", FRIDAY_DATA, fetcher, SATURDAY)
    assert writes == []


def test_run_once_fills_the_history_file(tmp_path):
    fetcher = DaysFetcher(recent=FRIDAY_DATA, full=LONG_HISTORY)
    csm.run_once([nysi_colour()], tmp_path, None, fetch=fetcher, today=SATURDAY, keep_history=True)
    assert len(csm.load_history(tmp_path / "NYSI_history.csv")[0]) == len(LONG_HISTORY) + len(FRIDAY_DATA)


def test_report_latest_days_and_runs():
    report = csm.build_report("NYSI", LONG_HISTORY, window_days=None)
    assert report["data_starts"] == "2026-11-02"
    assert report["latest"] == {"date": "2026-11-11", "value": 9.5, "change": 0.5, "colour": "Black",
                                "streak_trading_days": 1, "streak_complete": True}
    assert [d["colour"] for d in report["days"]] == ["Black", "Black", "Red", "Red", "Red", "Red", "Black"]
    assert report["days"][5] == {"date": "2026-11-10", "value": 9.0, "change": 0.0, "colour": "Red"}
    assert [(r["colour"], r["start"], r["end"], r["trading_days"]) for r in report["runs"]] == [
        ("Black", "2026-11-03", "2026-11-04", 2),
        ("Red", "2026-11-05", "2026-11-10", 4),
        ("Black", "2026-11-11", "2026-11-11", 1),
    ]
    assert report["runs"][0]["starts_at_data_start"] is True
    assert report["runs"][-1]["ongoing"] is True


def test_report_window_keeps_full_length_of_a_run_that_started_earlier():
    report = csm.build_report("NYSI", LONG_HISTORY, window_days=3)   # 2026-11-08 onwards
    assert [d["date"] for d in report["days"]] == ["2026-11-09", "2026-11-10", "2026-11-11"]
    red = report["runs"][0]
    assert (red["colour"], red["trading_days"], red["starts_before_window"]) == ("Red", 4, True)
    assert report["window"]["trading_days"] == 3


def test_summary_report_leaves_out_the_days():
    report = csm.build_report("NYSI", LONG_HISTORY, summary=True)
    assert "days" not in report and report["runs"]


def test_report_needs_two_points():
    assert csm.build_report("NYSI", FRIDAY_DATA[:1]) is None


def test_http_server_serves_history_and_errors(tmp_path):
    import urllib.error
    import urllib.request

    path = tmp_path / "NYSI_history.csv"
    server = csm.start_http_server(0, tmp_path, ["NYSI"])
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as no_data:
            urllib.request.urlopen(f"{base}/history")
        assert no_data.value.code == 503

        csm.save_history(path, LONG_HISTORY, SATURDAY)
        body = json.loads(urllib.request.urlopen(f"{base}/history?days=all&summary=1").read())
        assert body["latest"]["colour"] == "Black" and "days" not in body

        with pytest.raises(urllib.error.HTTPError) as bad_days:
            urllib.request.urlopen(f"{base}/history?days=-3")
        assert bad_days.value.code == 400
        assert json.loads(urllib.request.urlopen(f"{base}/health").read()) == {"ok": True, "symbols": ["NYSI"]}
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Watches and the levels rule
# ---------------------------------------------------------------------------

NYMO_LEVELS = csm.LevelsRule([-50, 50])


def nymo_levels(frequency="change"):
    return csm.Watch("NYMO", NYMO_LEVELS, frequency)


def test_parse_watches_reads_one_per_line_with_comments():
    watches = csm.parse_watches("""
        NYSI  colour            change
        # a comment
        $nymo levels 50 -50     daily   # levels in any order
    """)
    assert [w.describe() for w in watches] == ["NYSI colour change", "NYMO levels -50 50 daily"]


def test_parse_watches_accepts_semicolons_and_defaults_to_change():
    watches = csm.parse_watches("NYSI colour; NYMO levels -50 50")
    assert [w.frequency for w in watches] == ["change", "change"]


def test_parse_watches_understands_the_old_frequency_names():
    assert csm.parse_watches("NYSI colour colour_change")[0].frequency == "change"
    assert csm.parse_watches("NYSI colour new_data")[0].frequency == "daily"


@pytest.mark.parametrize("bad", [
    "NYSI",                          # no rule
    "NYSI sparkles",                 # unknown rule
    "NYSI colour 5",                 # colour takes no numbers
    "NYMO levels",                   # levels needs numbers
    "NYMO levels -50 fifty",         # not a number
    "NYMO levels -50 50 hourly",     # unknown frequency
    "NYSI colour; NYSI colour daily",  # same symbol and rule twice
    "  # only a comment",
])
def test_parse_watches_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        csm.parse_watches(bad)


def test_without_watches_it_is_one_colour_watch_like_before():
    class Args:
        watches = None
        symbol = "$nysi"
        email_frequency = "colour_change"
    [watch] = csm.load_watches(Args())
    assert watch.describe() == "NYSI colour change"


def test_levels_labels_each_side():
    data = points(("2026-11-17", 0.0), ("2026-11-18", -62.0), ("2026-11-19", 10.0), ("2026-11-20", 51.0))
    assert [label for _, label in nymo_levels().labels(data)] == [
        "below -50", "between -50 and 50", "above 50"]


def test_a_value_exactly_on_a_level_counts_as_below_it():
    data = points(("2026-11-19", 0.0), ("2026-11-20", 50.0))
    assert nymo_levels().latest_label(data) == "between -50 and 50"


def test_one_level_gives_two_sides():
    rule = csm.LevelsRule([0])
    assert rule.labels == ["below 0", "above 0"]
    assert rule.describe() == "levels 0"


def test_levels_transition_names_the_level_crossed():
    assert NYMO_LEVELS.transition("between -50 and 50", "below -50") == "crossed below -50"
    assert NYMO_LEVELS.transition("below -50", "between -50 and 50") == "crossed above -50"
    assert NYMO_LEVELS.transition("between -50 and 50", "above 50") == "crossed above 50"
    assert NYMO_LEVELS.transition("above 50", "between -50 and 50") == "crossed below 50"
    assert NYMO_LEVELS.transition("below -50", "above 50") == "crossed above 50"   # both in one day


def run_nymo(tmp_path, data, mailer, signal=None, today=SATURDAY):
    return csm.run_once([nymo_levels()], tmp_path, mailer, fetch=FakeFetcher(data), today=today, signal=signal)


def test_nymo_alerts_on_each_cross_out_and_back(tmp_path):
    values = [10, -20, -55, -70, -60, -40, -30, 20, 55, 60, 45]
    days = [
        "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09",
        "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16",
    ]
    series = points(*zip(days, values))
    mailer, signal = FakeMailer(), FakeSignal()
    for end in range(2, len(series) + 1):
        visible = series[:end]
        run_nymo(tmp_path, visible, mailer, signal, today=visible[-1].day)
        run_nymo(tmp_path, visible, mailer, signal, today=visible[-1].day)   # polled twice
    assert mailer.sent == [
        "NYMO Alert: crossed below -50, -55.00 (-35.00)",
        "NYMO Alert: crossed above -50, -40.00 (+20.00)",
        "NYMO Alert: crossed above 50, 55.00 (+35.00)",
        "NYMO Alert: crossed below 50, 45.00 (-15.00)",
    ]
    assert signal.sent[0] == "📉 NYMO crossed below -50: -55.00 (-35.00) on 2026-11-04. Between -50 and 50 lasted 1+ day."
    assert signal.sent[1] == "📈 NYMO crossed above -50: -40.00 (+20.00) on 2026-11-09. Below -50 lasted 3 days."


def test_nymo_daily_message_says_which_side_and_for_how_long():
    data = points(("2026-11-17", -50.0), ("2026-11-18", -60.0), ("2026-11-19", -10.0), ("2026-11-20", -5.0))
    text = csm.build_signal_message(nymo_levels("daily"), data, "between -50 and 50", "between -50 and 50")
    assert text == "📊 NYMO -5.00 (+5.00) on 2026-11-20, between -50 and 50 for 2 days in a row."
    subject, html = csm.build_email("daily", nymo_levels("daily"), data, "between -50 and 50", "between -50 and 50")
    assert subject == "NYMO Daily Update: between -50 and 50, -5.00 (+5.00)"
    assert "<strong>Level:</strong> between -50 and 50 for 2 days in a row" in html


def test_levels_state_has_its_own_file_and_remembers_the_rule(tmp_path):
    run_nymo(tmp_path, FRIDAY_DATA, FakeMailer())
    state = csm.load_state(tmp_path / "NYMO_levels_state.json")
    assert (state.last_label, state.rule) == ("between -50 and 50", "levels -50 50")


def test_changing_the_levels_starts_afresh_without_an_alert(tmp_path):
    mailer = FakeMailer()
    run_nymo(tmp_path, points(("2026-11-19", 0.0), ("2026-11-20", -60.0)), mailer)   # below -50
    wider = csm.Watch("NYMO", csm.LevelsRule([-70, 70]))
    data = points(("2026-11-20", -60.0), ("2026-11-23", -65.0))
    csm.run_once([wider], tmp_path, mailer, fetch=FakeFetcher(data), today=TUESDAY)
    assert mailer.sent == []
    assert csm.load_state(tmp_path / "NYMO_levels_state.json").rule == "levels -70 70"


def test_pre_v3_colour_state_carries_over(tmp_path):
    (tmp_path / "NYSI_state.json").write_text(json.dumps(
        {"last_date": "2026-11-20", "last_colour": "Black", "last_value": 3.0, "stale_alert_sent": False}))
    mailer = FakeMailer()
    run(tmp_path, FLIP_TO_RED, mailer, today=TUESDAY)
    assert mailer.sent == ["NYSI Alert: Black → Red, 1.00 (-2.00)"]


def test_watches_on_the_same_symbol_share_one_download(tmp_path):
    fetcher = FakeFetcher(FRIDAY_DATA)
    zero_line = csm.Watch("NYSI", csm.LevelsRule([0]))
    csm.run_once([nysi_colour(), zero_line], tmp_path, None, fetch=fetcher, today=SATURDAY)
    assert fetcher.calls == 1
    assert (tmp_path / "NYSI_state.json").exists() and (tmp_path / "NYSI_levels_state.json").exists()


def test_each_symbol_is_downloaded_and_a_failure_does_not_stop_the_others(tmp_path):
    asked = []

    def fetch(symbol, days):
        asked.append(symbol)
        if symbol == "NYSI":
            raise OSError("network down")
        return FRIDAY_DATA

    csm.run_once([nysi_colour(), nymo_levels()], tmp_path, None, fetch=fetch, today=SATURDAY)
    assert asked == ["NYSI", "NYMO"]
    assert not (tmp_path / "NYSI_state.json").exists()
    assert csm.load_state(tmp_path / "NYMO_levels_state.json").last_date == "2026-11-20"


def test_schedule_waits_for_the_symbol_that_is_furthest_behind(tmp_path):
    watches = [nysi_colour(), nymo_levels()]
    assert csm.oldest_data_day(watches, tmp_path) is None
    csm.save_state(tmp_path / "NYSI_state.json", State(last_date="2026-11-23", last_label="Red"))
    csm.save_state(tmp_path / "NYMO_levels_state.json", State(last_date="2026-11-20", last_label="below -50"))
    assert csm.oldest_data_day(watches, tmp_path) == date(2026, 11, 20)


def test_http_history_is_per_symbol(tmp_path):
    import urllib.error
    import urllib.request

    csm.save_history(tmp_path / "NYSI_history.csv", LONG_HISTORY, SATURDAY)
    csm.save_history(tmp_path / "NYMO_history.csv", FRIDAY_DATA, SATURDAY)
    server = csm.start_http_server(0, tmp_path, ["NYMO", "NYSI"])
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert json.loads(urllib.request.urlopen(f"{base}/history").read())["symbol"] == "NYSI"
        nymo = json.loads(urllib.request.urlopen(f"{base}/history?symbol=$nymo&summary=1").read())
        assert (nymo["symbol"], nymo["latest"]["value"]) == ("NYMO", 3.0)
        with pytest.raises(urllib.error.HTTPError) as unknown:
            urllib.request.urlopen(f"{base}/history?symbol=SPX")
        assert unknown.value.code == 404
    finally:
        server.shutdown()

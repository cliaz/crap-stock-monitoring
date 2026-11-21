"""
Tests for crap_stock_monitor.py

Run them with:
    pip install -r requirements-dev.txt
    python -m pytest
"""

import json
from datetime import date

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
    original = State(last_date="2026-11-19", last_colour="Red", last_value=-463.11, stale_alert_sent=True)
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
    assert csm.load_state(path) == State(last_date="2025-12-19", last_colour="Red", last_value=9.0)


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


def run(tmp_path, data, mailer, frequency="colour_change", today=SATURDAY, error=None):
    return csm.run_once(
        state_path=tmp_path / "NYSI_state.json",
        symbol="NYSI",
        email_frequency=frequency,
        mailer=mailer,
        fetch=FakeFetcher(data, error),
        today=today,
    )


def test_first_run_saves_state_without_emailing(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer)
    assert mailer.sent == []
    state = csm.load_state(tmp_path / "NYSI_state.json")
    assert state.last_date == "2026-11-20"
    assert state.last_colour == "Black"


def test_flip_sends_exactly_one_email(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer)
    run(tmp_path, FLIP_TO_RED, mailer, today=TUESDAY)
    run(tmp_path, FLIP_TO_RED, mailer, today=TUESDAY)   # same data polled again
    assert len(mailer.sent) == 1
    assert "Black to Red" in mailer.sent[0]


def test_no_flip_sends_nothing_in_colour_change_mode(tmp_path):
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
    assert "Daily" in mailer.sent[0]


def test_daily_mode_says_when_it_flipped(tmp_path):
    mailer = FakeMailer()
    run(tmp_path, FRIDAY_DATA, mailer, frequency="daily")
    run(tmp_path, FLIP_TO_RED, mailer, frequency="daily", today=TUESDAY)
    assert "Black to Red" in mailer.sent[0]


def test_failed_email_is_retried_on_the_next_check(tmp_path):
    run(tmp_path, FRIDAY_DATA, FakeMailer())
    broken = FakeMailer(fail=True)
    run(tmp_path, FLIP_TO_RED, broken, today=TUESDAY)
    assert csm.load_state(tmp_path / "NYSI_state.json").last_colour == "Black"   # not advanced

    working = FakeMailer()
    run(tmp_path, FLIP_TO_RED, working, today=TUESDAY)
    assert len(working.sent) == 1
    assert csm.load_state(tmp_path / "NYSI_state.json").last_colour == "Red"


def test_without_email_config_state_still_advances(tmp_path):
    run(tmp_path, FRIDAY_DATA, None)
    run(tmp_path, FLIP_TO_RED, None, today=TUESDAY)
    assert csm.load_state(tmp_path / "NYSI_state.json").last_colour == "Red"


def test_fetch_failure_does_not_crash_or_touch_state(tmp_path):
    run(tmp_path, FRIDAY_DATA, FakeMailer())
    run(tmp_path, None, FakeMailer(), today=TUESDAY, error=OSError("network down"))
    assert csm.load_state(tmp_path / "NYSI_state.json").last_date == "2026-11-20"


def test_check_fetches_data_only_once(tmp_path):
    fetcher = FakeFetcher(FRIDAY_DATA)
    csm.run_once(tmp_path / "s.json", "NYSI", "colour_change", None, fetch=fetcher, today=SATURDAY)
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
        "NYSI Alert: colour changed from Black to Red",
        "NYSI Alert: colour changed from Red to Black",
        "NYSI Alert: colour changed from Black to Red",
        "NYSI Alert: colour changed from Red to Black",
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

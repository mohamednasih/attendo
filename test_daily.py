from datetime import date, datetime, timedelta
from types import SimpleNamespace
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from daily import daily_total, day_window, period_log_totals, report, main
from intra_client import parse_locations


class DailyTests(unittest.TestCase):
    def test_period_log_totals_keep_intra_zero_and_missing_dates_distinct(self):
        period = SimpleNamespace(start='2026-10-30', end='2026-11-02')
        locations = parse_locations({'2026-10-29': '02:00:00', '2026-10-30': '01:25:10.5',
                                     '2026-11-01': '00:00:00', '2026-11-03': '04:00:00'})
        self.assertEqual(period_log_totals(period, locations), {
            '2026-10-30': 5110.5, '2026-10-31': None, '2026-11-01': 0, '2026-11-02': None,
        })

    def test_yesterday_intra_estimate_keeps_attendo_recorded_total(self):
        zone = ZoneInfo('Africa/Casablanca')
        fetched = datetime(2026, 11, 15, 12, tzinfo=zone)
        day = fetched.date() - timedelta(days=1)
        locations = parse_locations({day.isoformat(): '02:04:30.5'})
        recorded = SimpleNamespace(date=day.isoformat(), hours=2)
        profile = SimpleNamespace(login='student', fetched_at=fetched,
                                  previous=SimpleNamespace(days=[recorded]))
        start, end = day_window(day, zone, fetched.timestamp())
        result = report(profile, day, zone, [(start + 60, start + 120), (end, end + 60)],
                        fetched.timestamp(), [], intra_estimate=(locations[day], fetched))
        self.assertEqual(result['time_kind'], 'estimated')
        self.assertEqual(result['attendo_seconds'], 7470.5)
        self.assertEqual(result['logged_seconds'], 7410.5)
        self.assertEqual(result['attendo_recorded_seconds'], 7200)

    def test_today_recorded_time_is_separate_from_live_estimate(self):
        zone = ZoneInfo('Africa/Casablanca')
        now = datetime(2026, 11, 15, 12, tzinfo=zone)
        profile = SimpleNamespace(login='student', fetched_at=now, live_estimate='03:00',
                    current=SimpleNamespace(days=[SimpleNamespace(date='2026-11-15', hours=2.5)]),
                    previous=None)
        result = report(profile, now.date(), zone,
                        [(now.timestamp()-600, now.timestamp())], now.timestamp(), [])
        self.assertEqual(result['attendo_recorded_seconds'], 9000)
        self.assertEqual(result['attendo_seconds'], 10800)
        self.assertEqual(result['logged_seconds'], 10200)
        profile.today_hours = 2
        self.assertEqual(report(profile, now.date(), zone, [], now.timestamp(), [])['attendo_recorded_seconds'], 7200)
        profile.today_hours = None
        profile.current.days = []
        self.assertIsNone(report(profile, now.date(), zone, [], now.timestamp(), [])['attendo_recorded_seconds'])
        profile.current.days = [SimpleNamespace(date='2026-11-15', hours=0)]
        self.assertEqual(report(profile, now.date(), zone, [], now.timestamp(), [])['attendo_recorded_seconds'], 0)

    def test_shared_home_files_are_used_automatically(self):
        profile = SimpleNamespace(live_estimate='01:00', login='student',
                                  fetched_at=datetime(2026, 11, 15, 12, tzinfo=ZoneInfo('Africa/Casablanca')))
        client = SimpleNamespace(ensure_browser_session=lambda: None,
                                 fetch_profile=lambda: profile, AttendoError=RuntimeError,
                                 attendance_goal=lambda p: None)
        cutoff = profile.fetched_at.timestamp()
        output = StringIO()
        with patch('sys.argv', ['daily.py', '--once']), \
             patch('daily.HistoryCollector') as collector, \
             patch('daily.time.time', return_value=cutoff + 3600), \
             patch('daily.client', client), \
             patch('daily.read_state', return_value=[]), \
             patch('daily.legacy_history', return_value=[]), \
             patch('daily.fetch_history', return_value=([(cutoff-30, cutoff)], [])), redirect_stdout(output):
            collector.return_value.error = None
            main()
            collector.return_value.start.assert_called_once()
            collector.return_value.stop.assert_called_once()
        result = json.loads(output.getvalue())
        self.assertEqual(result['history_source'], 'shared_home')
        self.assertEqual(result['logged_seconds'], 3570)

    def test_stats_prints_once_without_live_refresh(self):
        zone = ZoneInfo('Africa/Casablanca')
        fetched = datetime(2026, 11, 15, 12, tzinfo=zone)
        profile = SimpleNamespace(live_estimate='02:00', login='student', fetched_at=fetched)
        output = StringIO()
        with patch('sys.argv', ['logged-time', 'stats']), \
             patch('daily.HistoryCollector') as collector, \
             patch('daily.time.time', return_value=fetched.timestamp()), \
             patch('daily.time.sleep') as sleep, \
             patch('daily.client.ensure_browser_session'), \
             patch('daily.client.fetch_profile', return_value=profile) as fetch, \
             patch('daily.client.attendance_goal', return_value=None), \
             patch('daily.read_state', return_value=[]), \
             patch('daily.legacy_history', return_value=[]), \
             patch('daily.fetch_history', return_value=([(fetched.timestamp()-600, fetched.timestamp())], [])), \
             redirect_stdout(output):
            collector.return_value.error = None
            main()
        text = output.getvalue()
        self.assertIn('Logged time', text)
        self.assertIn('01:50:00', text)
        self.assertIn('Locked time', text)
        self.assertNotIn('Lock deduction', text)
        self.assertIn('00:10:00', text)
        self.assertNotIn('refresh every', text)
        fetch.assert_called_once()
        sleep.assert_not_called()
        collector.return_value.stop.assert_called_once()

    def test_preserves_live_minutes(self):
        day = date(2026, 11, 15)
        profile = SimpleNamespace(live_estimate='07:35')
        self.assertEqual(daily_total(profile, day, day), (27300, 'todayHoursEstimation', 60))
        profile.live_estimate = None
        self.assertEqual(daily_total(profile, day, day), (None, None, None))

    def test_locks_crossing_midnight(self):
        zone = ZoneInfo('Africa/Casablanca')
        day = date(2026, 11, 15)
        now = datetime(2026, 11, 15, 12, tzinfo=zone).timestamp()
        start, end = day_window(day, zone, now)
        profile = SimpleNamespace(live_estimate='02:09', login='student', fetched_at=datetime.fromtimestamp(now, zone))
        result = report(profile, day, zone, [(start-60, start+60), (now-30, None)], now, [])
        self.assertEqual(result['locked_seconds'], 90)
        self.assertEqual(result['logged_seconds'], 7650)

    def test_casablanca_clock_change(self):
        zone = ZoneInfo('Africa/Casablanca')
        start, end = day_window(date(2026, 2, 15), zone, 1800000000)
        self.assertEqual(end-start, 25*3600)

    def test_cached_total_does_not_deduct_later_locks(self):
        zone = ZoneInfo('Africa/Casablanca')
        day = date(2026, 11, 15)
        fetched = datetime(2026, 11, 15, 12, tzinfo=zone)
        cutoff = fetched.timestamp()
        profile = SimpleNamespace(live_estimate='02:00', login='student', fetched_at=fetched)
        intervals = [(cutoff-30, None), (cutoff+60, cutoff+120)]
        result = report(profile, day, zone, intervals, cutoff+600, [])
        self.assertEqual(result['locked_seconds'], 30)
        self.assertEqual(result['logged_seconds'], 7170)
        self.assertEqual(result['accounted_through'], fetched.isoformat())

    def test_previous_day_fetch_cannot_be_used_as_live_total(self):
        zone = ZoneInfo('Africa/Casablanca')
        profile = SimpleNamespace(live_estimate='02:00', login='student',
                                  fetched_at=datetime(2026, 11, 14, 23, 59, tzinfo=zone))
        with self.assertRaises(ValueError):
            report(profile, date(2026, 11, 15), zone, [],
                   datetime(2026, 11, 15, 0, 1, tzinfo=zone).timestamp(), [])

    def test_historical_locks_are_observed_but_not_deducted_twice(self):
        zone = ZoneInfo('Africa/Casablanca')
        day = date(2026, 11, 14)
        now = datetime(2026, 11, 15, 12, tzinfo=zone)
        _, end = day_window(day, zone, now.timestamp())
        profile = SimpleNamespace(login='student', fetched_at=now, previous=None,
                    current=SimpleNamespace(days=[SimpleNamespace(date=str(day), hours=2)]))
        result = report(profile, day, zone, [(end-30, None)], now.timestamp(), [])
        self.assertEqual(result['locked_seconds'], 30)
        self.assertEqual(result['lock_deduction_seconds'], 0)
        self.assertEqual(result['logged_seconds'], 7200)
        self.assertEqual(result['time_kind'], 'recorded')

    def test_historical_daily_total(self):
        day = date(2026, 11, 14)
        profile = SimpleNamespace(current=SimpleNamespace(days=[SimpleNamespace(date=str(day), hours=2.5)]), previous=None)
        self.assertEqual(daily_total(profile, day, date(2026, 11, 15))[0], 9000)
        self.assertEqual(daily_total(profile, date(2026, 11, 13), date(2026, 11, 15)),
                         (None, 'daily.hours', None))


if __name__ == '__main__':
    unittest.main()

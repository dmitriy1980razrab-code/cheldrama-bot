from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from datetime import date
import unittest
from theatre_bot.marketing_reports import report_period,comparison_rows

class ReportPeriodTests(unittest.TestCase):
    def test_season_starts_september_first(self):
        p=report_period({'period':'season','date':'2026-09-01'},today=date(2026,9,1))
        self.assertEqual((p['from'],p['requested_to']),('2026-09-01','2027-07-10'))
        self.assertEqual((p['to'],p['previous_to']),('2026-09-01','2025-09-01'))
    def test_july_tenth_is_included(self):
        p=report_period({'period':'season','date':'2027-07-10'},today=date(2027,7,10))
        self.assertEqual((p['from'],p['to']),('2026-09-01','2027-07-10'))
        self.assertFalse(p['partial'])
    def test_offseason_points_to_last_completed_season(self):
        for day in ('2027-07-11','2027-08-31'):
            p=report_period({'period':'season','date':day},today=date.fromisoformat(day))
            self.assertEqual((p['from'],p['to']),('2026-09-01','2027-07-10'))
    def test_previous_season(self):
        p=report_period({'period':'previous_season','date':'2026-10-26'},today=date(2026,10,26))
        self.assertEqual((p['from'],p['to']),('2025-09-01','2026-07-10'))
        self.assertEqual((p['previous_from'],p['previous_to']),('2024-09-01','2025-07-10'))
    def test_week_monday_to_sunday(self):
        p=report_period({'period':'week','date':'2026-10-25'},today=date(2026,10,26))
        self.assertEqual((p['from'],p['to']),('2026-10-19','2026-10-25'))
        self.assertEqual((p['previous_from'],p['previous_to']),('2026-10-12','2026-10-18'))
    def test_current_week_compares_equal_elapsed_days(self):
        p=report_period({'period':'week','date':'2026-10-28'},today=date(2026,10,28))
        self.assertEqual((p['from'],p['to']),('2026-10-26','2026-10-28'))
        self.assertEqual((p['previous_from'],p['previous_to']),('2026-10-19','2026-10-21'))
    def test_leap_february_and_month_year_rollover(self):
        p=report_period({'period':'month','date':'2024-02-29'},today=date(2026,10,26))
        self.assertEqual(p['to'],'2024-02-29')
        p=report_period({'period':'previous_month','date':'2026-01-12'},today=date(2026,10,26))
        self.assertEqual((p['from'],p['to']),('2025-12-01','2025-12-31'))
    def test_custom_previous_equal_length(self):
        p=report_period({'period':'custom','from':'2026-10-10','to':'2026-10-12'},today=date(2026,10,26))
        self.assertEqual((p['previous_from'],p['previous_to']),('2026-10-07','2026-10-09'))
    def test_invalid_period_and_dates(self):
        for q in ({'period':'other'},{'period':'season','date':'invalid'},{'period':'custom','from':'2026-11-01','to':'2026-01-01'}):
            with self.assertRaises(ValueError):report_period(q)
    def test_snapshot_is_not_compared_to_false_historical_snapshot(self):
        rows=comparison_rows([('Премьеры сейчас','a','b',4),('Обращения за период','c','d',3)],
                             [('Премьеры сейчас','a','b',4),('Обращения за период','c','d',1)])
        self.assertIn(('Премьеры сейчас','a','b',4,'—','—'),rows)
        self.assertIn(('Обращения за период','c','d',3,1,2),rows)

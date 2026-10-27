from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
import io
import unittest
import zipfile
from urllib.parse import parse_qs,urlparse
from html.parser import HTMLParser
from theatre_bot.database import connect,initialize
from theatre_bot.subscribers import connect_subscribers,initialize_subscribers
from theatre_bot.marketing_reports import report_rows,report_period
from theatre_bot.admin_panel import report_page
from theatre_bot.report_excel import report_xlsx
from xml.etree import ElementTree as ET

class Links(HTMLParser):
    def __init__(self):super().__init__();self.urls=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.urls.append(dict(attrs).get('href',''))

class ReportFilterTests(unittest.TestCase):
    def setUp(self):
        self.db=connect_subscribers(':memory:');initialize_subscribers(self.db);self.addCleanup(self.db.close)
        self.theatre=connect(':memory:');initialize(self.theatre);self.addCleanup(self.theatre.close)
        self.user={'active':1,'role':'observer'}
        with self.theatre:
            self.theatre.executemany('INSERT INTO plays(source_url,title,normalized_title,synced_at) VALUES(?,?,?,?)',[
                ('one','Гамлет','гамлет','2026-10-01'),('two','Обломов','обломов','2026-10-01')])
        with self.db:
            self.db.execute("INSERT INTO subscribers(id,channel,external_id_hash,status,created_at,updated_at) VALUES(1,'vk','hash','active','2026-10-01','2026-10-01')")
            self.db.executemany('''INSERT INTO subscriptions(subscriber_id,topic_type,topic_key,topic_label,selected_starts_at,status,subscribed_at,unsubscribed_at)
                VALUES(1,'play',?,?,?,?,?,?)''',[
                ('p1','Гамлет','2026-11-20T18:00','unsubscribed','2026-10-10T08:00:00+00:00','2026-10-11T08:00:00+00:00'),
                ('p2','Обломов','2026-11-21T18:00','active','2026-10-12T08:00:00+00:00',None)])
            self.db.executemany('''INSERT INTO preferences(subscriber_id,preference_type,preference_key,preference_label,source,recorded_at)
                VALUES(1,?,?,?,'interest_survey','2026-10-10T08:00:00+00:00')''',[
                ('genre','g1','Комедия'),('genre','g2','Драма'),('artist','a1','Артист')])
            self.db.executemany('''INSERT INTO preference_changes(preference_type,preference_label,action,recorded_at)
                VALUES(?,?,?,'2026-10-10T00:00:00+00:00')''',[
                ('genre','Комедия','added'),('genre','Драма','removed'),('artist','Комедия','added')])
    def test_filters_only_relevant_sections_and_distinguish_performance_dates(self):
        rows=report_rows(self.db,self.user,'2026-10-01','2026-10-31',play='Гамлет',genre='Комедия')
        self.assertTrue(any(r[0]=='Подписки на показы за период' and 'Гамлет · 20.11.2026 18:00' in r[2] for r in rows))
        self.assertTrue(any(r[0]=='Отписки от показов за период' and 'Гамлет' in r[2] for r in rows))
        self.assertFalse(any('Обломов' in str(r) for r in rows))
        self.assertFalse(any(r[0]=='Приоритеты сейчас' and r[2] in ('Драма','Артист') for r in rows))
        self.assertEqual(sum(r[3] for r in rows if r[0]=='Изменения предпочтений за период'),1)
        self.assertTrue(any(r[0]=='Профили сейчас' for r in rows))
    def test_unfiltered_report_retains_other_plays_and_genres(self):
        rows=report_rows(self.db,self.user,'2026-10-01','2026-10-31')
        self.assertTrue(any('Обломов' in str(row) for row in rows))
        self.assertTrue(any(row[2]=='Драма' for row in rows))
    def test_panel_catalog_and_links_keep_both_filters(self):
        query={'period':'season','date':'2026-10-26','play':'Гамлет','genre':'Комедия'}
        body,rows=report_page(self.db,self.user,query,self.theatre)
        self.assertIn('Все спектакли',body);self.assertIn('Обломов',body)
        self.assertIn('Все жанры',body);self.assertIn('Комедия',body)
        links=Links();links.feed(body)
        for extension in ('xlsx','csv'):
            url=next(u for u in links.urls if u.startswith('/reports.'+extension))
            query_parts=parse_qs(urlparse(url).query)
            self.assertEqual(query_parts['play'],['Гамлет']);self.assertEqual(query_parts['genre'],['Комедия'])
    def test_workbook_labels_filter_and_keeps_genres_and_shows_separate(self):
        rows=report_rows(self.db,self.user,'2026-10-01','2026-10-31',play='Гамлет',genre='Комедия')
        from theatre_bot.marketing_reports import comparison_rows
        period=report_period({'period':'custom','from':'2026-10-01','to':'2026-10-31'})
        period.update(play='Гамлет',genre='Комедия')
        with zipfile.ZipFile(io.BytesIO(report_xlsx(period,comparison_rows(rows,())))) as z:
            ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            def texts(index):
                tree=ET.fromstring(z.read('xl/worksheets/sheet'+str(index)+'.xml'))
                return [t.text or '' for t in tree.findall('.//s:t',ns)]
            self.assertTrue(any('Спектакль: Гамлет; жанр: Комедия' in t for t in texts(2)))
            self.assertIn('Жанр или артист',texts(2))
            self.assertIn('Комедия',texts(2));self.assertNotIn('Драма',texts(2))
            self.assertTrue(any('Данные о покупке билетов пока не подключены' in t for t in texts(3)))
            self.assertIn('Спектакль и показ',texts(3))
            self.assertTrue(any('Гамлет · 20.11.2026 18:00' in t for t in texts(3)))
            self.assertFalse(any('Обломов' in t for t in texts(3)))
    def test_unknown_genre_is_rejected(self):
        with self.assertRaises(ValueError):report_page(self.db,self.user,{'genre':'неизвестный'},self.theatre)

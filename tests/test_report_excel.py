from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
import io
import unittest
import zipfile
from xml.etree import ElementTree as ET
from theatre_bot.report_excel import report_xlsx,HEADERS
from theatre_bot.marketing_reports import report_period
from datetime import date

class ReportExcelTests(unittest.TestCase):
    def setUp(self):self.period=report_period({'period':'season','date':'2026-10-26'},today=date(2026,10,26))
    def parts(self,rows):
        with zipfile.ZipFile(io.BytesIO(report_xlsx(self.period,rows))) as z:
            self.assertIsNone(z.testzip())
            return {name:ET.fromstring(z.read(name)) for name in z.namelist()}
    def test_plain_columns_and_frozen_headers(self):
        parts=self.parts([('Показатель','Группа','Значение',2,1,1)])
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        sheet=parts['xl/worksheets/sheet1.xml']
        self.assertEqual(sheet.find('s:sheetViews/s:sheetView/s:pane',ns).get('topLeftCell'),'A7')
        self.assertIsNone(sheet.find('s:autoFilter',ns))
        self.assertIsNone(sheet.find('s:tableParts',ns))
        self.assertFalse(any(name.startswith('xl/tables/') for name in parts))
        self.assertEqual([t.text for t in sheet.findall("s:sheetData/s:row[@r='6']/s:c/s:is/s:t",ns)],HEADERS)
        cells={c.get('r'):c for c in sheet.findall('s:sheetData/s:row/s:c',ns)}
        self.assertEqual(cells['D7'].find('s:v',ns).text,'2')
        self.assertNotIn('t',cells['D7'].attrib)
    def test_user_text_is_literal_not_formula(self):
        parts=self.parts([('=HYPERLINK("bad")','<b>&тест','текст\x01',3,1,-2)])
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        sheet=parts['xl/worksheets/sheet1.xml']
        self.assertEqual(sheet.findall('.//s:f',ns),[])
        texts=[t.text for t in sheet.findall('.//s:t',ns)]
        self.assertIn('=HYPERLINK("bad")',texts);self.assertIn('<b>&тест',texts);self.assertIn('текст',texts)
    def test_empty_report_is_valid_table(self):
        self.parts([])
    def test_wrong_column_count_rejected(self):
        with self.assertRaises(ValueError):report_xlsx(self.period,[('one','two')])

    def test_categories_are_separate_sheets_and_consents_are_not_mixed(self):
        rows=[('Профили сейчас','vk','Активен',1,'—','—'),
              ('Приоритеты сейчас','Жанр','Комедия',3,'—','—'),
              ('Подписки на показы за период','подписка','Гамлет · 01.11.2026 18:00',1,0,1),
              ('Согласия за период','Реклама','Отозвано',6,0,6),
              ('Согласия за период','Сервисные уведомления','Выдано',4,0,4)]
        parts=self.parts(rows)
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        names=[s.get('name') for s in parts['xl/workbook.xml'].find('s:sheets',ns)]
        self.assertEqual(names,['Аудитория','Интересы','Подписки на показы','Реклама','Согласия на уведомления','Обращения'])
        def text(i):return ' '.join(t.text or '' for t in parts[f'xl/worksheets/sheet{i}.xml'].findall('.//s:t',ns))
        self.assertIn('Комедия',text(2));self.assertNotIn('Гамлет',text(2))
        self.assertIn('Гамлет',text(3));self.assertNotIn('Отозвано',text(3))
        self.assertIn('Отозвано',text(4));self.assertNotIn('Сервисные уведомления',text(4))
        self.assertIn('Сервисные уведомления',text(5));self.assertNotIn('Отозвано',text(5))

    def test_count_format_does_not_force_trailing_decimal_separator(self):
        parts=self.parts([])
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        style=parts['xl/styles.xml'].find('s:cellXfs',ns)[4]
        self.assertEqual(style.get('numFmtId'),'0')

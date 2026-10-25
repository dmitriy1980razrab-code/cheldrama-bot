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
    def test_native_columns_filter_and_frozen_headers(self):
        parts=self.parts([('Показатель','Группа','Значение',2,1,1)])
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        sheet=parts['xl/worksheets/sheet1.xml'];table=parts['xl/tables/table1.xml']
        self.assertEqual(sheet.find('s:sheetViews/s:sheetView/s:pane',ns).get('topLeftCell'),'A7')
        self.assertEqual(table.get('ref'),'A6:F7')
        self.assertEqual(table.find('s:autoFilter',ns).get('ref'),'A6:F7')
        self.assertEqual([c.get('name') for c in table.find('s:tableColumns',ns)],HEADERS)
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

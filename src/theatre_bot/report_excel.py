"""Native XLSX report export using the Python standard library (no server dependency)."""
import io
import math
import re
import zipfile
from xml.etree import ElementTree as ET

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
HEADERS=['Показатель','Группа','Значение','За период / сейчас','Предыдущий период','Изменение']


def clean(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:32767]


def report_xlsx(period,rows):
    def node(tag,**attrs):return ET.Element('{'+NS+'}'+tag,attrs)
    sheet=node('worksheet')
    views=ET.SubElement(sheet,'{'+NS+'}sheetViews')
    view=ET.SubElement(views,'{'+NS+'}sheetView',workbookViewId='0',showGridLines='0')
    ET.SubElement(view,'{'+NS+'}pane',ySplit='6',topLeftCell='A7',activePane='bottomLeft',state='frozen')
    columns=ET.SubElement(sheet,'{'+NS+'}cols')
    for i,width in enumerate((55,27,42,24,24,20),1):
        ET.SubElement(columns,'{'+NS+'}col',min=str(i),max=str(i),width=str(width),customWidth='1')
    data=ET.SubElement(sheet,'{'+NS+'}sheetData')
    def add(number,values,style,height):
        row=ET.SubElement(data,'{'+NS+'}row',r=str(number),ht=str(height),customHeight='1')
        for col,value in enumerate(values):
            cell=ET.SubElement(row,'{'+NS+'}c',r=f'{chr(65+col)}{number}',s=str(style if col<3 or style!=3 else 4))
            if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):
                ET.SubElement(cell,'{'+NS+'}v').text=str(value)
            else:
                cell.set('t','inlineStr')
                text=ET.SubElement(ET.SubElement(cell,'{'+NS+'}is'),'{'+NS+'}t')
                text.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
                text.text=clean('' if value is None else value)
    add(1,[period['title']],1,30)
    add(2,[f"Отчёт: {period['from']} — {period['to']}"],2,23)
    add(3,[f"Сравнение: {period['previous_from']} — {period['previous_to']}"],2,23)
    add(4,['Строки «сейчас» — текущий срез. История изменений собирается с установки панели; отсутствие старых данных не доказывает отсутствие событий.'],2,35)
    add(6,HEADERS,5,34)
    values=list(rows) or [('Нет данных за выбранный период','','','','','')]
    for index,row in enumerate(values,7):
        if len(row)!=6:raise ValueError('Expected six report columns')
        add(index,row,3,max(32,min(90,16*(1+max(len(clean(v))//w for v,w in zip(row,(52,25,40,22,22,18)))))))
    merges=ET.SubElement(sheet,'{'+NS+'}mergeCells',count='4')
    for row in range(1,5):ET.SubElement(merges,'{'+NS+'}mergeCell',ref=f'A{row}:F{row}')
    parts=ET.SubElement(sheet,'{'+NS+'}tableParts',count='1')
    ET.SubElement(parts,'{'+NS+'}tablePart',{'{'+REL+'}id':'rId1'})
    end=6+len(values)
    table=node('table',id='1',name='TheatreReport',displayName='TheatreReport',ref=f'A6:F{end}',totalsRowShown='0')
    ET.SubElement(table,'{'+NS+'}autoFilter',ref=f'A6:F{end}')
    cols=ET.SubElement(table,'{'+NS+'}tableColumns',count='6')
    for i,name in enumerate(HEADERS,1):ET.SubElement(cols,'{'+NS+'}tableColumn',id=str(i),name=name)
    ET.SubElement(table,'{'+NS+'}tableStyleInfo',name='TableStyleMedium2',showFirstColumn='0',showLastColumn='0',showRowStripes='1',showColumnStripes='0')
    styles=f'''<styleSheet xmlns="{NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.##"/></numFmts>
    <fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="18"/><color rgb="FF1769AA"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
    <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1769AA"/><bgColor indexed="64"/></patternFill></fill></fills>
    <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
    <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
    <cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs>
    <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    content_types='''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'''
    for part,kind in [('workbook','sheet.main'),('worksheets/sheet1','worksheet'),('styles','styles'),('tables/table1','table')]:
        content_types+=f'<Override PartName="/xl/{part}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml"/>'
    content_types+='</Types>'
    def rels(items):return '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="{key}" Type="{REL}/{kind}" Target="{target}"/>' for key,kind,target in items)+'</Relationships>'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as out:
        out.writestr('[Content_Types].xml',content_types)
        out.writestr('_rels/.rels',rels([('rId1','officeDocument','xl/workbook.xml')]))
        out.writestr('xl/workbook.xml',f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets><sheet name="Отчёт" sheetId="1" r:id="rId1"/></sheets></workbook>')
        out.writestr('xl/_rels/workbook.xml.rels',rels([('rId1','worksheet','worksheets/sheet1.xml'),('rId2','styles','styles.xml')]))
        out.writestr('xl/worksheets/sheet1.xml',ET.tostring(sheet,encoding='utf-8',xml_declaration=True))
        out.writestr('xl/worksheets/_rels/sheet1.xml.rels',rels([('rId1','table','../tables/table1.xml')]))
        out.writestr('xl/tables/table1.xml',ET.tostring(table,encoding='utf-8',xml_declaration=True))
        out.writestr('xl/styles.xml',styles)
    return buffer.getvalue()

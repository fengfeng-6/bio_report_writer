from __future__ import annotations

import io
import re
import zipfile
from html import escape
from pathlib import Path
from xml.etree import ElementTree

from .document_blocks import DocumentBlock, parse_report_markdown
from .markdown_report import render_markdown_report
from .models import ReportIR
from .report_validation import validate_report_markdown


CONTENT_WIDTH_DXA = 8731
TABLE_INDENT_DXA = 0
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _clean_text(text: str) -> str:
    return "".join(character for character in text if character in "\t\n\r" or ord(character) >= 32)


def _text(text: str) -> str:
    return escape(_clean_text(text), quote=False)


def _run(text: str, *, bold: bool = False, color: str | None = None) -> str:
    properties = [
        '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="SimSun"/>',
        '<w:lang w:val="zh-CN" w:eastAsia="zh-CN"/>',
    ]
    if bold:
        properties.append("<w:b/>")
    if color:
        properties.append(f'<w:color w:val="{color}"/>')
    return f'<w:r><w:rPr>{"".join(properties)}</w:rPr><w:t xml:space="preserve">{_text(text)}</w:t></w:r>'


def _paragraph(text: str, style: str = "Normal", *, keep_next: bool = False) -> str:
    keep = "<w:keepNext/>" if keep_next else ""
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{keep}</w:pPr>{_run(text)}</w:p>'


def _bullet(text: str) -> str:
    return (
        '<w:p><w:pPr><w:pStyle w:val="ListParagraph"/>'
        '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        f'</w:pPr>{_run(text)}</w:p>'
    )


def _cell(text: str, width: int, *, header: bool) -> str:
    shading = '<w:shd w:val="clear" w:color="auto" w:fill="F4F6F9"/>' if header else ""
    bold = header
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shading}'
        '<w:vAlign w:val="center"/></w:tcPr>'
        f'<w:p><w:pPr><w:pStyle w:val="TableText"/></w:pPr>{_run(text, bold=bold)}</w:p></w:tc>'
    )


def _table(block: DocumentBlock) -> str:
    column_count = max(1, len(block.rows[0]))
    base_width, remainder = divmod(CONTENT_WIDTH_DXA, column_count)
    widths = [base_width + (1 if index < remainder else 0) for index in range(column_count)]
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    rows: list[str] = []
    for row_index, row in enumerate(block.rows):
        header = row_index == 0
        row_properties = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
        cells = "".join(_cell(value, widths[index], header=header) for index, value in enumerate(row))
        rows.append(f"<w:tr>{row_properties}{cells}</w:tr>")
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="B7C3D0"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    return (
        '<w:tbl><w:tblPr>'
        f'<w:tblW w:w="{CONTENT_WIDTH_DXA}" w:type="dxa"/>'
        f'<w:tblInd w:w="{TABLE_INDENT_DXA}" w:type="dxa"/>'
        '<w:tblLayout w:type="fixed"/>'
        f'<w:tblBorders>{borders}</w:tblBorders>'
        '<w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:start w:w="120" w:type="dxa"/>'
        '<w:bottom w:w="80" w:type="dxa"/><w:end w:w="120" w:type="dxa"/></w:tblCellMar>'
        f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>'
    )

def _image_paragraph(relationship_id: str, name: str, source: str) -> str:
    payload = Path(source).read_bytes()
    width_px = int.from_bytes(payload[16:20], "big") if payload.startswith(b"\x89PNG") else 1600
    height_px = int.from_bytes(payload[20:24], "big") if payload.startswith(b"\x89PNG") else 900
    width_emu = 5486400
    height_emu = min(5029200, max(914400, int(width_emu * height_px / max(width_px, 1))))
    return (
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
        '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width_emu}" cy="{height_emu}"/>'
        f'<wp:docPr id="1" name="{_text(name)}"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="0" name="chart.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{relationship_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    )


def _document_xml(blocks: list[DocumentBlock]) -> str:
    body: list[str] = []
    image_index = 0
    for block in blocks:
        if block.kind == "pagebreak":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        elif block.kind == "image":
            image_index += 1
            body.append(_image_paragraph(f"rId{6 + image_index}", block.text, block.source))
        elif block.kind == "heading":
            style = {1: "Title", 2: "Heading1", 3: "Heading2", 4: "Heading3"}.get(block.level, "Heading3")
            body.append(_paragraph(block.text, style, keep_next=block.level > 1))
        elif block.kind == "bullet":
            body.append(_bullet(block.text))
        elif block.kind == "table":
            body.append(_table(block))
            body.append(_paragraph("", "TableCitation"))
        else:
            body.append(_paragraph(block.text))
    section = (
        '<w:sectPr>'
        '<w:titlePg/><w:headerReference w:type="default" r:id="rId1"/>'
        '<w:footerReference w:type="default" r:id="rId2"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1474" w:bottom="1440" w:left="1701" '
        'w:header="850" w:footer="992" w:gutter="0"/>'
        '<w:cols w:space="720"/><w:docGrid w:linePitch="312"/>'
        '</w:sectPr>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORD_NS}" xmlns:r="{REL_NS}"><w:body>'
        f'{"".join(body)}{section}</w:body></w:document>'
    )


def _styles_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{WORD_NS}">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="SimSun"/><w:sz w:val="24"/><w:szCs w:val="24"/><w:lang w:val="zh-CN" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:jc w:val="both"/><w:spacing w:before="0" w:after="0" w:line="360" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="SimSun"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:before="2880" w:after="240"/><w:keepNext/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="SimHei"/><w:b/><w:color w:val="000000"/><w:sz w:val="44"/><w:szCs w:val="44"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="360" w:after="240"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="SimHei"/><w:b/><w:color w:val="000000"/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="120"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="SimHei"/><w:b/><w:color w:val="000000"/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="120" w:after="120"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="SimHei"/><w:color w:val="000000"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="0" w:after="80" w:line="290" w:lineRule="auto"/><w:contextualSpacing/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="left"/><w:spacing w:before="0" w:after="0" w:line="280" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="TableCitation"><w:name w:val="Table Citation"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="80" w:after="80"/></w:pPr><w:rPr><w:sz w:val="18"/><w:szCs w:val="18"/><w:color w:val="666666"/></w:rPr></w:style>
</w:styles>'''


def _numbering_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{WORD_NS}">
  <w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="540"/></w:tabs><w:ind w:left="540" w:hanging="279"/><w:spacing w:after="80" w:line="290" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr></w:lvl></w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>'''


def _header_xml(project_name: str, target_year: int) -> str:
    label = f"{project_name} | {target_year}年生态修复报告"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:hdr xmlns:w="{WORD_NS}"><w:p><w:pPr><w:jc w:val="right"/>'
        '<w:spacing w:after="0"/></w:pPr>'
        f'{_run(label, color="667788")}</w:p></w:hdr>'
    )


def _footer_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="{WORD_NS}"><w:p><w:pPr><w:jc w:val="center"/><w:spacing w:before="0" w:after="0"/></w:pPr><w:r><w:t>第 </w:t></w:r><w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple><w:r><w:t> 页</w:t></w:r></w:p></w:ftr>'''


def _zip_member(archive: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, content.encode("utf-8"))

def _zip_binary(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, content)


def render_docx_bytes(markdown: str, project_name: str, target_year: int) -> bytes:
    blocks = parse_report_markdown(markdown)
    image_blocks = [block for block in blocks if block.kind == "image"]
    image_relationships = "".join(f'<Relationship Id="rId{7 + index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{index + 1}.png"/>' for index in range(len(image_blocks)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        _zip_member(archive, "[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>''')
        _zip_member(archive, "_rels/.rels", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PACKAGE_REL_NS}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>''')
        _zip_member(archive, "word/document.xml", _document_xml(blocks))
        _zip_member(archive, "word/styles.xml", _styles_xml())
        _zip_member(archive, "word/numbering.xml", _numbering_xml())
        _zip_member(archive, "word/settings.xml", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings xmlns:w="{WORD_NS}"><w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/><w:characterSpacingControl w:val="doNotCompress"/><w:compat/></w:settings>''')
        _zip_member(archive, "word/fontTable.xml", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:fonts xmlns:w="{WORD_NS}"><w:font w:name="Times New Roman"/><w:font w:name="Arial"/><w:font w:name="SimSun"><w:charset w:val="86"/><w:family w:val="roman"/></w:font><w:font w:name="SimHei"><w:charset w:val="86"/><w:family w:val="swiss"/></w:font></w:fonts>''')
        _zip_member(archive, "word/header1.xml", _header_xml(project_name, target_year))
        _zip_member(archive, "word/footer1.xml", _footer_xml())
        _zip_member(archive, "word/_rels/document.xml.rels", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PACKAGE_REL_NS}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/><Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/><Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>{image_relationships}</Relationships>''')
        for index, block in enumerate(image_blocks, start=1):
            _zip_binary(archive, f"word/media/image{index}.png", Path(block.source).read_bytes())
        _zip_member(archive, "docProps/core.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>生态状况诊断与生态修复建议报告</dc:title><dc:creator>Eco Report Agent</dc:creator></cp:coreProperties>''')
        _zip_member(archive, "docProps/app.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Eco Report Agent</Application></Properties>''')
    return buffer.getvalue()


def validate_docx_bytes(payload: bytes) -> tuple[str, ...]:
    issues: list[str] = []
    required = {"[Content_Types].xml", "word/document.xml", "word/styles.xml", "word/numbering.xml"}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            missing = required - set(archive.namelist())
            if missing:
                issues.append("missing package members: " + ", ".join(sorted(missing)))
                return tuple(issues)
            document = ElementTree.fromstring(archive.read("word/document.xml"))
            styles = ElementTree.fromstring(archive.read("word/styles.xml"))
            numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        return (f"invalid OOXML package: {exc}",)
    ns = {"w": WORD_NS}
    if document.find(".//w:numPr", ns) is None:
        issues.append("real list numbering is missing")
    if numbering.find(".//w:abstractNum", ns) is None:
        issues.append("numbering definition is missing")
    style_ids = {item.get(f"{{{WORD_NS}}}styleId") for item in styles.findall("w:style", ns)}
    for style_id in ("Normal", "Title", "Heading1", "Heading2", "Heading3", "TableText"):
        if style_id not in style_ids:
            issues.append(f"style is missing: {style_id}")
    page_size = document.find(".//w:sectPr/w:pgSz", ns)
    margins = document.find(".//w:sectPr/w:pgMar", ns)
    if page_size is None or page_size.get(f"{{{WORD_NS}}}w") != "11906" or page_size.get(f"{{{WORD_NS}}}h") != "16838":
        issues.append("page size is not A4 portrait")
    expected_margins = {"top": "1440", "right": "1474", "bottom": "1440", "left": "1701"}
    if margins is None or any(margins.get(f"{{{WORD_NS}}}{side}") != value for side, value in expected_margins.items()):
        issues.append("page margins do not match report_style_config.json")
    for table in document.findall(".//w:tbl", ns):
        table_width = table.find("w:tblPr/w:tblW", ns)
        table_indent = table.find("w:tblPr/w:tblInd", ns)
        grid_widths = [int(item.get(f"{{{WORD_NS}}}w", "0")) for item in table.findall("w:tblGrid/w:gridCol", ns)]
        if table_width is None or table_width.get(f"{{{WORD_NS}}}w") != str(CONTENT_WIDTH_DXA):
            issues.append("table width does not match A4 content area")
        if table_indent is None or table_indent.get(f"{{{WORD_NS}}}w") != str(TABLE_INDENT_DXA):
            issues.append("table indent does not match style config")
        if sum(grid_widths) != CONTENT_WIDTH_DXA:
            issues.append("table grid does not match A4 content area")
    visible_text = "".join(item.text or "" for item in document.findall(".//w:t", ns))
    if not re.search(r"生态.*修复.*报告", visible_text):
        issues.append("report title is missing")
    return tuple(issues)


def build_docx_report(report: ReportIR) -> bytes:
    markdown = render_markdown_report(report)
    markdown_validation = validate_report_markdown(markdown, report)
    if not markdown_validation.valid:
        raise ValueError("Markdown report failed validation before DOCX rendering")
    project_name = report.project_name or f"FID {report.fid}项目"
    payload = render_docx_bytes(markdown, project_name, report.target_year)
    issues = validate_docx_bytes(payload)
    if issues:
        raise ValueError("DOCX validation failed: " + "; ".join(issues))
    return payload


def write_docx_report(report: ReportIR, output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_docx_report(report))
    return path

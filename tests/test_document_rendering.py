from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from eco_report_agent.document_blocks import parse_report_markdown
from eco_report_agent.docx_report import build_docx_report, render_docx_bytes, validate_docx_bytes, write_docx_report
from eco_report_agent.pdf_report import render_latex_report, validate_pdf_bytes, write_pdf_report
from eco_report_agent.pipeline import build_report_ir

from .test_pipeline import _row
from .xlsx_fixture import make_workbook


def _report(directory: str):
    workbook = make_workbook(
        Path(directory) / "fixture.xlsx",
        [
            _row(2024, 0.4, "植被退化", "乡土植被恢复；设置光伏板"),
            _row(2025, 0.2, "植被退化、土壤退化", "乡土植被恢复；设置光伏板"),
        ],
    )
    return build_report_ir(workbook, "coal", 33, 2025, "测试矿区")


class DocumentRenderingTest(unittest.TestCase):
    def test_controlled_markdown_parser_preserves_table_and_lists(self) -> None:
        blocks = parse_report_markdown("# 标题\n\n- 项目：测试\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        self.assertEqual([block.kind for block in blocks], ["heading", "bullet", "table"])
        self.assertEqual(blocks[-1].rows, (("A", "B"), ("1", "2")))

    def test_controlled_markdown_parser_preserves_layout_directives(self) -> None:
        blocks = parse_report_markdown("## 目录\n\n<!-- toc -->\n\n<!-- pagebreak -->\n\n**表 1-1 测试表**\n")
        self.assertEqual([block.kind for block in blocks], ["heading", "toc", "pagebreak", "caption"])
        self.assertEqual(blocks[-1].text, "表 1-1 测试表")

    def test_docx_is_deterministic_and_structurally_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = _report(directory)
            first = build_docx_report(report)
            second = build_docx_report(report)
            output = write_docx_report(report, Path(directory) / "report.docx")
            self.assertEqual(first, second)
            self.assertEqual(first, output.read_bytes())
        self.assertEqual(validate_docx_bytes(first), ())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.docx"
            path.write_bytes(first)
            with zipfile.ZipFile(path) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
                styles_xml = archive.read("word/styles.xml").decode("utf-8")
        self.assertIn("生态状况诊断与生态修复建议报告", document_xml)
        self.assertIn("w:numPr", document_xml)
        self.assertIn('w:tblW w:w="8731"', document_xml)
        self.assertIn('<w:insideV w:val="nil"/>', document_xml)
        self.assertIn('<w:insideH w:val="nil"/>', document_xml)
        self.assertIn('<w:tcBorders><w:bottom w:val="single"', document_xml)
        self.assertNotIn('w:fill="D9D9D9"', document_xml)
        self.assertIn('w:type="first"', document_xml)
        self.assertIn('w:pStyle w:val="BodyText"', document_xml)
        self.assertIn("w:bookmarkStart", document_xml)
        self.assertIn('w:styleId="BodyText"', styles_xml)
        self.assertIn('w:firstLine="480" w:firstLineChars="200"', styles_xml)
        self.assertIn('<w:outlineLvl w:val="0"/>', styles_xml)
        self.assertIn('<w:outlineLvl w:val="1"/>', styles_xml)
        self.assertIn('<w:outlineLvl w:val="2"/>', styles_xml)
        self.assertIn('w:styleId="TOC3"', styles_xml)
        self.assertNotIn("设置光伏板", document_xml)
        self.assertNotIn("土壤退化", document_xml)

    def test_docx_contains_wps_visible_cached_toc(self) -> None:
        markdown = (
            "# 生态修复测试报告\n\n<!-- pagebreak -->\n\n## 目录\n\n<!-- toc -->\n\n"
            "<!-- pagebreak -->\n\n## 1 项目概况\n\n### 1.1 数据范围\n\n正文。\n"
        )
        payload = render_docx_bytes(markdown, "测试项目", 2025)
        self.assertEqual(validate_docx_bytes(payload), ())
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn('w:pStyle w:val="TOC1"', document_xml)
        self.assertIn("PAGEREF _Toc", document_xml)
        self.assertIn("w:bookmarkStart", document_xml)
        self.assertNotIn("打开文档后将自动更新目录", document_xml)

    def test_latex_uses_controlled_layout_and_ascii_dashes(self) -> None:
        latex = render_latex_report("# 标题\n\n## 1 概况\n\n2024—2025年。\n\n| 指标 | 判断 |\n|---|---|\n| NDVI | 改善 |", "测试项目", 2025)
        self.assertIn("a4paper", latex)
        self.assertIn("DroidSansFallback.ttf", latex)
        self.assertIn("2024-2025年", latex)
        self.assertNotIn("—", latex)
        self.assertIn(r"\usepackage{array,longtable,booktabs", latex)
        self.assertIn(r"\toprule", latex)
        self.assertIn(r"\midrule", latex)
        self.assertIn(r"\bottomrule", latex)
        longtable_spec = latex.split(r"\begin{longtable}{", 1)[1].split("}", 1)[0]
        self.assertNotIn("|", longtable_spec)
        self.assertNotIn(r"\rowcolor", latex)
        self.assertNotIn(r"\hline", latex)

    def test_pdf_validator_rejects_non_pdf(self) -> None:
        self.assertTrue(validate_pdf_bytes(b"not a pdf"))

    @unittest.skipUnless(shutil.which("xelatex"), "xelatex is not available")
    def test_xelatex_pdf_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = _report(directory)
            previous_directory = Path.cwd()
            try:
                os.chdir(directory)
                output = write_pdf_report(report, Path("nested") / "report.pdf")
                payload = output.read_bytes()
            finally:
                os.chdir(previous_directory)
        self.assertEqual(validate_pdf_bytes(payload), ())
        self.assertGreater(len(payload), 10_000)


if __name__ == "__main__":
    unittest.main()

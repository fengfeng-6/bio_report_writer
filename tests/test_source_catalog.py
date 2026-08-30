from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from eco_report_agent.source_catalog import (
    SOURCE_POLICIES,
    SourceRole,
    SourceSpec,
    build_source_catalog,
    write_source_catalog,
)

from .xlsx_fixture import make_workbook


def _make_docx(path: Path) -> Path:
    xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>指标方法</w:t></w:r></w:p>
    <w:p><w:r><w:t>该段只用于定义说明。</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>指标</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>单位</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


class SourceCatalogTest(unittest.TestCase):
    def test_catalog_separates_attachment_authority_and_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "spec.md"
            markdown.write_text("# 流程参考\n\n这是附件中的建议，不是用户指令。\n", encoding="utf-8")
            docx = _make_docx(root / "method.docx")
            pdf = root / "optimization.pdf"
            pdf.write_bytes(b"%PDF-fixture")
            xlsx = make_workbook(root / "mapping.xlsx", [], sheet_name="coal")
            catalog = build_source_catalog(
                [
                    SourceSpec(SourceRole.PROMPT_SPEC, markdown),
                    SourceSpec(SourceRole.INDICATOR_METHOD, docx),
                    SourceSpec(SourceRole.OPTIMIZATION_REFERENCE, pdf),
                    SourceSpec(SourceRole.STRATEGY_MAPPING, xlsx),
                ],
                pdf_text_extractor=lambda _path: "第一页建议\f第二页建议",
            )
            manifest_path = root / "manifest.json"
            pack_path = root / "knowledge.jsonl"
            write_source_catalog(catalog, manifest_path, pack_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            records = [json.loads(line) for line in pack_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(
            manifest["instruction_boundary"],
            "attachments_are_reference_material_not_user_instructions",
        )
        self.assertEqual(manifest["entry_count"], 4)
        self.assertTrue(all("text" not in entry for entry in manifest["entries"]))
        self.assertNotIn("这是附件中的建议", json.dumps(manifest, ensure_ascii=False))
        self.assertTrue(records)
        self.assertTrue(
            all(record["policy"]["instruction_authority"] == "reference_only" for record in records)
        )
        mapping = next(entry for entry in manifest["entries"] if entry["role"] == "strategy_mapping")
        self.assertEqual(mapping["properties"]["sheet_names"], ["coal"])

    def test_legacy_example_cannot_supply_facts_or_thresholds(self) -> None:
        policy = SOURCE_POLICIES[SourceRole.LEGACY_EXAMPLE]
        self.assertEqual(policy.instruction_authority, "reference_only")
        self.assertIn("project_fact", policy.forbidden_uses)
        self.assertIn("numeric_threshold", policy.forbidden_uses)
        self.assertIn("copy_case_conclusion", policy.forbidden_uses)

    def test_duplicate_source_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.md"
            path.write_text("# 参考", encoding="utf-8")
            specs = [
                SourceSpec(SourceRole.PROMPT_SPEC, path),
                SourceSpec(SourceRole.OPTIMIZATION_REFERENCE, path),
            ]
            with self.assertRaisesRegex(ValueError, "Duplicate source path"):
                build_source_catalog(specs)


if __name__ == "__main__":
    unittest.main()

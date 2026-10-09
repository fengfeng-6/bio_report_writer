from scripts.export_reports_postgres import transform_report
from scripts.import_reports_postgres import load_bundle


def fact(name, values):
    return {"indicator": name, "valid": True, "time_series": [{"year": 2020 + i, "value": v} for i, v in enumerate(values)]}


def test_replaces_images_and_builds_frontend_data():
    markdown = "![a](/a.png)\ntext\n![b](/b.png)\n![c](/c.png)"
    ir = {"indicator_facts": [fact("MEAN_NDVI", [0.2, 0.3]), fact("MEAN_NPP", [1, 2]), fact("NP", [10, 12]), fact("PD", [2, 3]), fact("LPI", [20, 10])], "problem_diagnoses": [{"problem_id": "P1", "problem_name": "问题", "status": "突出问题", "severity": "high", "trend": "worsening"}]}
    content, charts = transform_report(markdown, ir)
    assert "![" not in content and "/a.png" not in content
    assert all(f"{{{{chart:{i}}}}}" in content for i in (1, 2, 3))
    assert [x["type"] for x in charts] == ["line", "line", "matrix"]
    assert charts[1]["data_json"]["series"][0]["points"][1]["value"] == 120.0


def test_load_bundle_validates_counts_and_foreign_keys(tmp_path):
    (tmp_path / "report_entry.ndjson").write_text('{"id":"abc","content":"x","type":"报告"}\n', encoding="utf-8")
    (tmp_path / "report_chart.ndjson").write_text('{"report_id":"abc","insert_key":1,"type":"line","data_json":{}}\n', encoding="utf-8")
    (tmp_path / "manifest.json").write_text('{"report_count":1,"chart_count":1,"failure_count":0}', encoding="utf-8")
    reports, charts = load_bundle(tmp_path)
    assert len(reports) == 1 and len(charts) == 1

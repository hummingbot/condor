import condor.reports as reports


def test_report_route_streams_only_html_inside_report_directory(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from condor.web.app import create_app

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report = reports_dir / "sample.html"
    report.write_text("<h1>Report</h1>", encoding="utf-8")
    (reports_dir / "private.txt").write_text("secret", encoding="utf-8")
    outside = tmp_path / "outside.html"
    outside.write_text("outside", encoding="utf-8")
    monkeypatch.setattr(reports, "CHARTS_DIR", reports_dir)

    with TestClient(create_app()) as client:
        response = client.get("/reports/sample.html")
        assert response.status_code == 200
        assert response.text == "<h1>Report</h1>"
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"
        assert client.get("/reports/private.txt").status_code == 404
        assert client.get("/reports/%2e%2e/outside.html").status_code == 404

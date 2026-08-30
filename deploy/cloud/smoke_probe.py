from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "http://eco-report-smoke-api:8000"
QUERY = urlencode(
    {
        "project_type": "coal",
        "fid": 900001,
        "target_year": 2025,
        "project_name": "cloud-smoke",
    }
)


def fetch(path: str, *, token: str | None = None) -> dict[str, object]:
    headers = {} if token is None else {"X-Report-Service-Token": token}
    request = Request(BASE_URL + path, headers=headers)
    try:
        with urlopen(request, timeout=15) as response:
            payload = response.read()
            return {
                "status": response.status,
                "content_type": response.headers.get("Content-Type"),
                "bytes": len(payload),
                "prefix": payload[:2].decode("ascii", errors="replace"),
            }
    except HTTPError as error:
        error.read()
        return {"status": error.code}


token = os.environ["ECO_REPORT_SERVICE_TOKEN"]
results = {
    "health": fetch("/health"),
    "unauthorized": fetch("/v1/report-markdown?" + QUERY),
    "markdown": fetch("/v1/report-markdown?" + QUERY, token=token),
    "docx": fetch("/v1/report-docx?" + QUERY, token=token),
    "pdf_disabled": fetch("/v1/report-pdf?" + QUERY, token=token),
}
print(json.dumps(results, ensure_ascii=False, sort_keys=True))

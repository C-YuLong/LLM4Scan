"""Submit the private competition ZIP or inspect a known submission.

Credentials and signed storage URLs remain in memory. Run records are private.
"""

import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://competition.nctieda.com:30080"


def login():
    raise PermissionError('Platform network access and submissions are paused by user instruction; no credentials will be read.')
    lines = (ROOT / "accounts.txt").read_text(encoding="utf-8-sig").splitlines()
    username, password = [
        re.sub(r"^[\u4e00-\u9fff\s\uff1a:]+", "", lines[i]).strip()
        for i in (4, 5)
    ]
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    page = client.open(ORIGIN + "/accounts/login/", timeout=25).read().decode()
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
    client.open(urllib.request.Request(
        ORIGIN + "/accounts/login/",
        data=urllib.parse.urlencode({
            "username": username, "password": password, "csrfmiddlewaretoken": token,
        }).encode(),
        headers={"Referer": ORIGIN + "/accounts/login/"},
    ), timeout=25).read()
    if not any(c.name == "sessionid" for c in jar):
        raise RuntimeError("Competition login failed")
    return client, next(c.value for c in jar if c.name == "csrftoken")


def api(client, csrf, path, method="GET", data=None):
    response = client.open(urllib.request.Request(
        ORIGIN + path, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Referer": ORIGIN + "/competitions/38/",
                 "X-CSRFToken": csrf, "Content-Type": "application/json"},
    ), timeout=45)
    body = response.read()
    return json.loads(body) if body else None


def summary(row):
    result = {k: row[k] for k in ("id", "status", "created_when", "started", "finished") if k in row}
    if row.get("children"):
        result["children"] = [summary(c) if isinstance(c, dict) else c for c in row["children"]]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--submit", type=Path)
    group.add_argument("--status", type=int)
    parser.add_argument("--watch", action="store_true", help="Wait for terminal status; check after 120s, then every 600s")
    parser.add_argument("--logs", type=Path, help="Save official logs to a new dev/results directory (with --status)")
    args = parser.parse_args()
    if args.logs and args.status is None:
        parser.error("--logs requires --status")
    if args.watch and args.status is None:
        parser.error("--watch requires --status")
    client, csrf = login()
    if args.status is not None:
        delay = 120
        while True:
            result = api(client, csrf, f"/api/submissions/{args.status}/")
            print(json.dumps(summary(result), ensure_ascii=False), flush=True)
            if not args.watch or result.get("status") in {"Finished", "Cancelled", "Failed", "Unknown"}:
                break
            time.sleep(delay)
            delay = 600
        if args.logs:
            output = args.logs.resolve()
            output.relative_to(ROOT / "dev" / "results")
            output.mkdir(parents=True, exist_ok=False)
            details = api(client, csrf, f"/api/submissions/{args.status}/get_details/")
            status = summary(result)
            status["scores"] = result.get("scores")
            status["artifact_availability"] = {
                key: bool(details.get(key)) for key in (
                    "prediction_result", "scoring_result", "detailed_result", "intermediate_files",
                )
            }
            (output / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            for log in details.get("logs", []):
                if not log.get("data_file"):
                    continue
                name = log["name"]
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    raise RuntimeError("Unexpected platform log filename")
                body = client.open(urllib.parse.urljoin(ORIGIN, log["data_file"]), timeout=60).read()
                (output / (name + ".log")).write_bytes(body)
                print(json.dumps({"log": name, "bytes": len(body)}))
        return

    archive = args.submit.resolve(strict=True)
    archive.relative_to(ROOT / "dev" / "results")
    record = archive.with_suffix(".platform.json")
    if record.exists():
        raise RuntimeError("Upload record exists; inspect it and existing submissions before retrying")
    permission = api(client, csrf, "/api/can_make_submission/38")
    if not permission.get("can"):
        raise RuntimeError("Platform does not currently allow submission")
    dataset = api(client, csrf, "/api/datasets/", "POST", {
        "type": "submission", "competition": 38, "is_public": False,
        "request_sassy_file_name": archive.name, "file_name": archive.name,
        "file_size": archive.stat().st_size,
    })
    state = {"competition": 38, "phase": 38, "archive": archive.name,
             "dataset_key": dataset["key"], "stage": "dataset_created"}
    record.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    destination = urllib.parse.urlsplit(dataset["sassy_url"])
    print(json.dumps({"upload_destination": f"{destination.scheme}://{destination.netloc}"}), flush=True)
    if destination.scheme not in ("http", "https") or destination.hostname != urllib.parse.urlsplit(ORIGIN).hostname:
        raise RuntimeError("Inspect the platform storage destination before sending the private ZIP")
    # The current competition advertises S3 storage, not Azure.
    archive_bytes = archive.read_bytes()
    urllib.request.urlopen(urllib.request.Request(
        dataset["sassy_url"], data=archive_bytes, method="PUT",
        headers={"Content-Type": "application/zip"},
    ), timeout=60).read()
    state["stage"] = "uploaded"
    record.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    try:
        api(client, csrf, f"/api/datasets/completed/{dataset['key']}/", "PUT")
    except urllib.error.HTTPError as error:
        if error.code != 500:
            raise
        # Observed platform bug: finalization returns 500 after a successful PUT.
        # Only proceed if the platform serves exactly the private bytes uploaded.
        downloaded = client.open(
            ORIGIN + f"/datasets/download/{dataset['key']}/", timeout=60,
        ).read()
        if downloaded != archive_bytes:
            raise RuntimeError("Platform upload does not match the local archive") from None
        state["upload_completion_http_status"] = error.code
        state["upload_verification"] = "Downloaded archive matches local bytes"
        record.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        print("Upload confirmation returned 500; exact downloaded bytes verified", flush=True)
    result = api(client, csrf, "/api/submissions/", "POST", {
        "data": dataset["key"], "phase": 38, "fact_sheet_answers": None,
        "tasks": [40], "organization": None, "queue": 6,
    })
    state.update(stage="submitted", submission=summary(result))
    record.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(state["submission"], ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        # Error bodies and URLs may contain storage signatures or credentials.
        raise SystemExit(f"Platform request failed: HTTP {error.code}; inspect existing records before retrying")
    except urllib.error.URLError as error:
        raise SystemExit(f"Platform connection failed: {type(error.reason).__name__}; inspect records before retrying")

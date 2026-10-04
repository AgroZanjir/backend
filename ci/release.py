"""Change one infra image reference, then wait for its exact deployment receipt."""
import argparse
import io
import json
import os
import signal
from pathlib import Path
import time
import urllib.parse
import zipfile

from github_api import GitHub, desired_line, image_parts, validate_receipt


def wait_for_deployment(client, app, commit, image, timeout=2700, run_id=None):
    deadline = time.monotonic() + timeout
    discovery_deadline = time.monotonic() + min(timeout, 120)
    query = urllib.parse.urlencode({"head_sha": commit, "event": "push", "per_page": 100})
    path = client.base + f"/actions/workflows/deploy-{app}.yml/runs?" + query
    last_status = None
    while time.monotonic() < deadline:
        if run_id is not None:
            run = client.request(client.base + f"/actions/runs/{run_id}")
            if str(run["id"]) != str(run_id) or run["event"] != "workflow_dispatch":
                raise ValueError("Unexpected dispatched infra workflow identity")
            commit = run["head_sha"] if commit is None else commit
            if run["head_sha"] != commit:
                raise ValueError("Dispatched infra workflow commit changed")
            runs = [run]
        else:
            runs = client.request(path)["workflow_runs"]
            runs = [run for run in runs if run["head_sha"] == commit and run["event"] == "push"]
        if runs:
            run = max(runs, key=lambda item: (item["id"], item.get("run_attempt", 1)))
            status = (run["id"], run["status"], run.get("conclusion"))
            if status != last_status:
                print(f"Infra deployment: {run['html_url']} ({run['status']})", flush=True)
                last_status = status
            if run["status"] == "completed":
                if run["conclusion"] != "success":
                    raise RuntimeError("Infra deployment did not succeed; image cleanup is disabled")
                artifacts = client.pages(client.base + f"/actions/runs/{run['id']}/artifacts", "artifacts")
                matches = [a for a in artifacts if a["name"] == "deployment-receipt" and not a["expired"]]
                if len(matches) != 1:
                    raise RuntimeError("Expected one unexpired deployment receipt artifact")
                archive = client.request(client.base + f"/actions/artifacts/{matches[0]['id']}/zip", raw=True)
                with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                    member = zipped.getinfo(f"{app}.json")
                    if member.file_size > 16384:
                        raise ValueError("Oversized deployment receipt")
                    receipt = validate_receipt(json.loads(zipped.read(member)), app, image)
                if receipt["infra_sha"] != commit or str(receipt["infra_run_id"]) != str(run["id"]) or str(receipt["infra_run_attempt"]) != str(run.get("run_attempt", 1)):
                    raise ValueError("Receipt does not match the exact infra workflow attempt")
                return receipt
        elif time.monotonic() >= discovery_deadline:
            raise TimeoutError("No infra deployment appeared within 2 minutes; check workflow triggers and Actions permissions")
        time.sleep(15)
    raise TimeoutError("Infra deployment did not finish within 45 minutes; cleanup is disabled")


def stop_on_signal(signum, _frame):
    print("Release wait cancelled; cleanup will not run.", flush=True)
    raise SystemExit(128 + signum)


def request_deployment(client, app, image, message):
    commit, changed = client.update_file(f"apps/{app}/image.env", desired_line(app, image),
                                       message, report_change=True)
    if changed:
        print(f"Updated {client.repository} at {commit}; awaiting push deployment", flush=True)
        return commit, None
    print("Desired image is unchanged; starting a fresh deployment from infra main", flush=True)
    result = client.request(client.base + f"/actions/workflows/deploy-{app}.yml/dispatches",
                            "POST", {"ref": "main", "inputs": {"operation": "deploy", "expected_image": image}})
    if not isinstance(result, dict) or not str(result.get("workflow_run_id", "")).isdigit():
        raise RuntimeError("GitHub did not return the dispatched deployment run ID")
    return None, result["workflow_run_id"]


def main():
    signal.signal(signal.SIGTERM, stop_on_signal)
    signal.signal(signal.SIGINT, stop_on_signal)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=("backend", "frontend"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--receipt", default="deployment-receipt.json")
    args = parser.parse_args()
    _, _, sha, run, _, _ = image_parts(args.image, args.app, os.environ["GITHUB_REPOSITORY"])
    if sha != os.environ["GITHUB_SHA"] or run != os.environ["GITHUB_RUN_ID"]:
        raise ValueError("Image is not from this source commit and workflow run")
    client = GitHub(os.environ["GH_TOKEN"], os.environ.get("INFRA_REPOSITORY", "AgroZanjir/infra"))
    commit, infra_run = request_deployment(client, args.app, args.image,
                                          f"deploy({args.app}): {sha} [run {run}]")
    receipt = wait_for_deployment(client, args.app, commit, args.image, run_id=infra_run)
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

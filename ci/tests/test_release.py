"""Safety invariants for destructive registry cleanup and cross-repo deployments."""
import io
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
import urllib.error
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cleanup import deletion_candidates, retained_graph
from github_api import GitHub, desired_line, image_parts, validate_receipt
from release import wait_for_deployment
import cleanup

SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
IMAGE = f"ghcr.io/agrozanjir/backend:backend-{SHA}-123-1@{DIGEST}"


def receipt():
    return {"schema": 1, "app": "backend", "current": IMAGE, "previous": None,
            "release_id": IMAGE, "source_sha": SHA, "infra_sha": "c" * 40,
            "infra_run_id": "456", "infra_run_attempt": "1", "deployed_at": "2026-10-04T12:00:00Z"}


class ReleaseTests(unittest.TestCase):
    def test_stale_cleanup_never_opens_registry_or_deletes(self):
        newer = receipt() | {"infra_run_attempt": "2"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text(json.dumps(receipt()))
            with patch("sys.argv", ["cleanup", "--app", "backend", "--receipt", str(path)]), \
                    patch.dict("os.environ", {"GITHUB_REPOSITORY": "AgroZanjir/backend", "GH_TOKEN": "test"}), \
                    patch.object(cleanup, "GitHub") as api, patch.object(cleanup, "Registry") as registry:
                api.return_value.file.return_value = (json.dumps(newer), "blob")
                cleanup.main()
                registry.assert_not_called()
                api.return_value.request.assert_not_called()

    def test_ref_requires_digest_full_sha_and_matching_repository(self):
        self.assertEqual(image_parts(IMAGE, "backend", "AgroZanjir/backend")[-1], DIGEST)
        for invalid in (IMAGE.split("@")[0], IMAGE.replace(SHA, "aaaa"), IMAGE + "\n"):
            with self.assertRaises(ValueError):
                image_parts(invalid)
        with self.assertRaises(ValueError):
            image_parts(IMAGE, "frontend")

    def test_receipt_rejects_other_app_or_unexpected_secret_field(self):
        validate_receipt(receipt(), "backend", IMAGE)
        with self.assertRaises(ValueError):
            validate_receipt(receipt(), "frontend")
        altered = receipt() | {"runtime_env": "never upload this"}
        with self.assertRaises(ValueError):
            validate_receipt(altered, "backend")

    def test_retains_index_platform_and_attestation_children(self):
        child, attestation, old = ["sha256:" + value * 64 for value in ("c", "d", "e")]
        manifests = {DIGEST: {"schemaVersion": 2, "manifests": [{"digest": child}, {"digest": attestation}]},
                     child: {"schemaVersion": 2}, attestation: {"schemaVersion": 2}, old: {"schemaVersion": 2}}
        retained = retained_graph({DIGEST, old}, manifests.__getitem__)
        self.assertEqual(retained, {DIGEST, child, attestation, old})
        versions = [{"id": 1, "name": child}, {"id": 2, "name": attestation}, {"id": 3, "name": "unretained"}]
        self.assertEqual(deletion_candidates(versions, retained), [3])

    def test_unreadable_manifest_aborts_before_cleanup(self):
        with self.assertRaises(KeyError):
            retained_graph({DIGEST}, {}.__getitem__)

    def test_paginate_all_versions_instead_of_first_100(self):
        client = GitHub("test", "owner/repository")
        with patch.object(client, "request", side_effect=[list(range(100)), list(range(100, 200)), [200]]):
            self.assertEqual(len(client.pages("/versions")), 201)

    def test_update_retries_conflict_without_overwriting_other_app(self):
        client = GitHub("test", "owner/infra")
        conflict = urllib.error.HTTPError("https://api.github.com", 409, "conflict", {}, None)
        with patch.object(client, "file", side_effect=[("old\n", "blob1"), ("old\n", "blob2")]), \
                patch.object(client, "request", side_effect=[conflict, {"commit": {"sha": SHA}}]) as request, \
                patch("github_api.time.sleep"):
            self.assertEqual(client.update_file("apps/backend/image.env", desired_line("backend", IMAGE), "release"), SHA)
            self.assertEqual(request.call_args.args[2]["sha"], "blob2")

    def test_rollback_compare_and_swap_does_not_overwrite_new_release(self):
        client = GitHub("test", "owner/infra")
        with patch.object(client, "file", return_value=("new image\n", "blob")):
            with self.assertRaises(RuntimeError):
                client.update_file("apps/backend/image.env", "rollback\n", "rollback", expected="old\n")

    def test_failed_infra_run_never_produces_cleanup_receipt(self):
        client = GitHub("test", "owner/infra")
        run = {"id": 456, "head_sha": "c" * 40, "event": "push", "status": "completed", "conclusion": "failure", "html_url": "https://github.com/owner/infra/actions/runs/456"}
        with patch.object(client, "request", return_value={"workflow_runs": [run]}):
            with self.assertRaises(RuntimeError):
                wait_for_deployment(client, "backend", "c" * 40, IMAGE)

    def test_receipt_must_match_exact_infra_run_attempt(self):
        client = GitHub("test", "owner/infra")
        run = {"id": 456, "run_attempt": 2, "head_sha": "c" * 40, "event": "push", "status": "completed", "conclusion": "success", "html_url": "https://github.com/owner/infra/actions/runs/456"}
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("backend.json", json.dumps(receipt()))
        with patch.object(client, "request", side_effect=[{"workflow_runs": [run]}, content.getvalue()]), \
                patch.object(client, "pages", return_value=[{"id": 1, "name": "deployment-receipt", "expired": False}]):
            with self.assertRaises(ValueError):
                wait_for_deployment(client, "backend", "c" * 40, IMAGE)


if __name__ == "__main__":
    unittest.main()

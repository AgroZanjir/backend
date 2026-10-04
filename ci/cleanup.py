"""Retain current/previous release manifest graphs and delete other GHCR versions."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

from github_api import GitHub, SafeRedirect, image_parts, validate_receipt

ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json", "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json", "application/vnd.docker.distribution.manifest.v2+json"))


class Registry:
    def __init__(self, repository, username, token):
        self.repository = repository
        query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
        auth = base64.b64encode(f"{username}:{token}".encode()).decode()
        request = urllib.request.Request("https://ghcr.io/token?" + query, headers={"Authorization": "Basic " + auth})
        self.opener = urllib.request.build_opener(SafeRedirect())
        with self.opener.open(request, timeout=30) as response:
            self.token = json.load(response)["token"]

    def manifest(self, digest):
        request = urllib.request.Request(f"https://ghcr.io/v2/{self.repository}/manifests/{digest}",
                                         headers={"Authorization": "Bearer " + self.token, "Accept": ACCEPT})
        with self.opener.open(request, timeout=30) as response:
            raw = response.read()
        if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Registry manifest did not match its requested digest")
        return json.loads(raw)


def retained_graph(roots, fetch):
    """Preserve platform and attestation manifests; blobs are registry-managed."""
    retained, pending = set(), list(roots)
    while pending:
        digest = pending.pop()
        if digest in retained:
            continue
        if not __import__("re").fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ValueError("Unexpected manifest digest")
        retained.add(digest)
        manifest = fetch(digest)  # Fail closed before deleting anything if graph is unreadable.
        if manifest.get("schemaVersion") != 2:
            raise ValueError("Unsupported registry manifest")
        for child in manifest.get("manifests", []):
            pending.append(child["digest"])
    return retained


def deletion_candidates(versions, retained):
    return [version["id"] for version in versions if version["name"] not in retained]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=("backend", "frontend"), required=True)
    parser.add_argument("--receipt", default="deployment-receipt.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    receipt = validate_receipt(json.loads(Path(args.receipt).read_text()), args.app)
    repository, _, _, _, _, current = image_parts(receipt["current"], args.app, os.environ["GITHUB_REPOSITORY"])
    infra = GitHub(os.environ["GH_TOKEN"], os.environ.get("INFRA_REPOSITORY", "AgroZanjir/infra"))
    state_path = f"apps/{args.app}/deployed.json"
    state = validate_receipt(json.loads(infra.file(state_path)[0]), args.app)
    if state != receipt:
        print("Cleanup skipped: this deployment receipt has been superseded.")
        return
    roots = {current}
    if receipt["previous"]:
        roots.add(image_parts(receipt["previous"], args.app, repository)[-1])
    registry = Registry(repository, os.environ["GITHUB_ACTOR"], os.environ["GHCR_TOKEN"])
    retained = retained_graph(roots, registry.manifest)
    packages = GitHub(os.environ["GHCR_TOKEN"], repository)
    owner, package = repository.split("/")
    owner_type = packages.request("/users/" + owner)["type"]
    prefix = "/orgs/" if owner_type == "Organization" else "/users/"
    path = prefix + owner + "/packages/container/" + urllib.parse.quote(package, safe="") + "/versions"
    versions = packages.pages(path)  # Snapshot all pages before any deletion shifts pagination.
    if not roots.issubset({version["name"] for version in versions}):
        raise RuntimeError("Retained release missing from package API; refusing cleanup")
    candidates = deletion_candidates(versions, retained)
    # A rollback can run independently; it is restricted to the same retained pair.
    # An old rerun must never prune after a newer publication/deployment.
    if validate_receipt(json.loads(infra.file(state_path)[0]), args.app) != receipt:
        print("Cleanup skipped: deployment state changed during inspection.")
        return
    print(f"Retaining {len(roots)} releases ({len(retained)} manifests); deleting {len(candidates)} versions.")
    for version_id in candidates:
        if not args.dry_run:
            packages.request(f"{path}/{version_id}", "DELETE")
    print("Dry run complete." if args.dry_run else "GHCR cleanup complete.")


if __name__ == "__main__":
    main()

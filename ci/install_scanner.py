"""Install pinned Linux amd64 scanner binaries after verifying release SHA256."""
import argparse
import hashlib
import io
import os
from pathlib import Path
import tarfile
import urllib.request

TOOLS = {
    "gitleaks": ("https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz",
                 "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"),
    "trivy": ("https://github.com/aquasecurity/trivy/releases/download/v0.75.0/trivy_0.75.0_Linux-64bit.tar.gz",
              "c6e65abddb348e25f10549df887045629cf28cc72453cd1c63acb717316b3f3f"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=TOOLS)
    args = parser.parse_args()
    url, expected = TOOLS[args.tool]
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Scanner archive checksum mismatch")
    destination = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "agrozanjir-tools"
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = archive.getmember(args.tool)
        if not member.isfile():
            raise ValueError("Scanner is not a regular file")
        binary = archive.extractfile(member).read()
    target = destination / args.tool
    target.write_bytes(binary)
    target.chmod(0o755)
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as output:
        output.write(str(destination) + "\n")


if __name__ == "__main__":
    main()

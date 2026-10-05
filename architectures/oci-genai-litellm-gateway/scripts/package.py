#!/usr/bin/env python3
"""Create a reproducible OCI Resource Manager ZIP from reviewed source files.

The manifest is intentionally explicit. Adding files to the repository does
not silently publish them in a customer deployment package.
"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
from pathlib import Path
import stat
from urllib.parse import quote, urlsplit
from zipfile import ZIP_STORED, ZipFile, ZipInfo

ARCHIVE_NAME = "oci-genai-phi-stack.zip"
START_MARKER = "<!-- DEPLOY_BUTTON_START -->"
END_MARKER = "<!-- DEPLOY_BUTTON_END -->"
BUTTON_IMAGE = "https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg"
CONSOLE_URL = "https://cloud.oracle.com/resourcemanager/stacks/create"

REQUIRED_FILES = (
    "main.tf",
    "outputs.tf",
    "variables.tf",
    "versions.tf",
    ".terraform.lock.hcl",
    "schema.yaml",
    "compose.yaml",
    "README.md",
    "deploy/cloud-init.yaml.tftpl",
    "gateway/__init__.py",
    "gateway/config.yaml",
    "gateway/oci_auth.py",
    "gateway/Dockerfile",
    "gateway/requirements.txt",
    "guardrail/__init__.py",
    "guardrail/app.py",
    "guardrail/recognizers.py",
    "guardrail/Dockerfile",
    "guardrail/requirements.txt",
    "examples/cost_controlled_chat.py",
    "examples/smoke_test.py",
    "scripts/package.py",
)

# Public documentation and verification sources are included when present.
# No globbing: local notes, .env files, state, keys and caches stay excluded.
OPTIONAL_FILES = (
    "terraform.tfvars.example",
    "requirements-dev.txt",
    "docs/PRD.md",
    "docs/customer-tenancy-deployment.md",
    "docs/deployment.md",
    "docs/litellm-routing-and-cost.md",
    "docs/reference-architecture.md",
    "docs/research.md",
    "docs/validation.md",
    "tests/test_gateway.py",
    "tests/test_guardrail.py",
    "tests/test_cost_controlled_chat.py",
    "tests/test_infra.py",
    "tests/test_package.py",
    ".github/workflows/validate.yml",
)


def button_markdown(package_url: str) -> str:
    """Construct Oracle's documented button URL with a safely encoded zipUrl."""
    if any(ord(char) <= 32 or ord(char) == 127 for char in package_url):
        raise ValueError("Package URL must be an absolute HTTPS URL without whitespace")
    try:
        parsed = urlsplit(package_url)
        valid = (
            parsed.scheme == "https" and parsed.hostname and parsed.netloc
            and parsed.username is None and parsed.password is None and not parsed.fragment
            and "\\" not in package_url
        )
        parsed.port  # Validate a supplied port instead of accepting malformed URLs.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Package URL must be an absolute HTTPS URL without credentials or a fragment")
    return f"[![Deploy to Oracle Cloud]({BUTTON_IMAGE})]({CONSOLE_URL}?zipUrl={quote(package_url, safe='')})"


def updated_readme(readme: str, package_url: str) -> str:
    """Replace only the explicitly marked button block."""
    button = button_markdown(package_url)
    if readme.count(START_MARKER) != 1 or readme.count(END_MARKER) != 1:
        raise ValueError("README must contain exactly one pair of deploy-button markers")
    before, after_start = readme.split(START_MARKER)
    _, after = after_start.split(END_MARKER)
    return f"{before}{START_MARKER}\n{button}\n{END_MARKER}{after}"


def _checked_path(root: Path, relative: str) -> Path:
    candidate = root / relative
    # Reject directory symlinks as well as file symlinks; neither belongs in a
    # public source package, even when its current target is inside the repo.
    for part in (candidate, *candidate.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError(f"Refusing to package a symlink: {relative}")
    return candidate


def build_package(root: Path, package_url: str | None = None) -> tuple[Path, Path]:
    """Build the package; only --package-url intentionally changes README."""
    root = Path(root).resolve()
    sources: dict[str, bytes] = {}
    for relative in sorted((*REQUIRED_FILES, *OPTIONAL_FILES)):
        candidate = _checked_path(root, relative)
        if not candidate.is_file():
            if relative in REQUIRED_FILES:
                raise ValueError(f"Required package source is missing: {relative}")
            continue
        sources[relative] = candidate.read_bytes()
    if package_url is not None:
        sources["README.md"] = updated_readme(sources["README.md"].decode("utf-8"), package_url).encode("utf-8")

    buffer = BytesIO()
    # ZIP_STORED avoids zlib-version differences. This small source-only stack
    # needs no compression; models and dependencies are installed at VM build.
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        for relative, content in sources.items():
            info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = ZIP_STORED
            archive.writestr(info, content)
    data = buffer.getvalue()
    destination = _checked_path(root, f"dist/{ARCHIVE_NAME}")
    checksum = _checked_path(root, f"dist/{ARCHIVE_NAME}.sha256")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    checksum.write_text(f"{hashlib.sha256(data).hexdigest()}  {ARCHIVE_NAME}\n", encoding="ascii")
    if package_url is not None:
        (root / "README.md").write_bytes(sources["README.md"])
    return destination, checksum


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-url",
        help="HTTPS URL where this ZIP will be hosted; updates the marked README button (does not upload)",
    )
    args = parser.parse_args()
    try:
        package, checksum = build_package(Path(__file__).resolve().parents[1], args.package_url)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Created {package}")
    print(f"Created {checksum}")
    if args.package_url:
        print("Updated README deploy button. Publish the ZIP at the configured URL before sharing the button.")


if __name__ == "__main__":
    main()

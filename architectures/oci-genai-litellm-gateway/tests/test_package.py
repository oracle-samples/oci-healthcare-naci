import hashlib
import os
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import pytest

from scripts.package import (
    ARCHIVE_NAME, END_MARKER, OPTIONAL_FILES, REQUIRED_FILES, START_MARKER,
    build_package, button_markdown, updated_readme,
)


@pytest.fixture
def source_tree(tmp_path):
    for relative in (*REQUIRED_FILES, *OPTIONAL_FILES):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"public source: {relative}\n")
    (tmp_path / "README.md").write_text(f"Before\n{START_MARKER}\nPending publication\n{END_MARKER}\nAfter\n")
    return tmp_path


def test_package_is_reproducible_and_has_terraform_at_zip_root(source_tree):
    original_readme = (source_tree / "README.md").read_bytes()
    archive, checksum = build_package(source_tree)
    first = archive.read_bytes()
    for relative in REQUIRED_FILES:
        os.utime(source_tree / relative, (1_700_000_000, 1_700_000_000))
        (source_tree / relative).chmod(0o600)
    second, _ = build_package(source_tree)
    assert second.read_bytes() == first
    assert checksum.read_text() == f"{hashlib.sha256(first).hexdigest()}  {ARCHIVE_NAME}\n"
    assert (source_tree / "README.md").read_bytes() == original_readme
    with ZipFile(archive) as package:
        assert package.namelist() == sorted((*REQUIRED_FILES, *OPTIONAL_FILES))
        assert "main.tf" in package.namelist()
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in package.infolist())
        assert all((info.external_attr >> 16) & 0o777 == 0o644 for info in package.infolist())


def test_package_excludes_local_files_and_secrets(source_tree):
    excluded = (
        ".env", ".local/customer-record.json", ".venv/lib/secret.py",
        "terraform.tfvars", "terraform.tfvars.json", "terraform.tfstate",
        "terraform.tfstate.backup", "private.pem", "gateway/private.key",
        "guardrail/__pycache__/app.pyc", "docs/private-notes.md",
        "extra.tf", "config/credentials", ".terraform/provider",
    )
    for relative in excluded:
        target = source_tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("DO_NOT_PACKAGE_SYNTHETIC_SECRET")
    archive, _ = build_package(source_tree)
    with ZipFile(archive) as package:
        assert not set(excluded) & set(package.namelist())
        assert all(b"DO_NOT_PACKAGE_SYNTHETIC_SECRET" not in package.read(name) for name in package.namelist())


def test_missing_required_source_fails_before_creating_artifact(source_tree):
    (source_tree / "main.tf").unlink()
    with pytest.raises(ValueError, match="Required package source"):
        build_package(source_tree)
    assert not (source_tree / "dist").exists()


@pytest.mark.parametrize("relative", ["main.tf", "docs/research.md"])
def test_symlink_cannot_smuggle_local_secrets(source_tree, relative):
    secret = source_tree / "secret.txt"
    secret.write_text("synthetic secret")
    target = source_tree / relative
    target.unlink()
    target.symlink_to(secret)
    with pytest.raises(ValueError, match="symlink"):
        build_package(source_tree)


def test_symlink_parent_is_rejected(source_tree):
    (source_tree / "docs").rename(source_tree / "private-docs")
    (source_tree / "docs").symlink_to(source_tree / "private-docs", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        build_package(source_tree)


@pytest.mark.parametrize("url", [
    "http://example.test/stack.zip", "file:///tmp/stack.zip", "//example.test/stack.zip",
    "https:///stack.zip", "https://user:password@example.test/stack.zip",
    "https://@example.test/stack.zip",
    "https://example.test/stack.zip#fragment", "https://example.test:bad/stack.zip",
    "https://example.test/with space.zip", "https://example.test/stack.zip\n",
])
def test_invalid_package_urls_are_rejected(url):
    with pytest.raises(ValueError, match="HTTPS"):
        button_markdown(url)


def test_button_preserves_nested_url_query_and_only_updates_markers(source_tree):
    package_url = "https://objectstorage.example.test/p/token/n/demo/b/releases/o/stack.zip?download=1&version=v1"
    archive, _ = build_package(source_tree, package_url)
    readme = (source_tree / "README.md").read_text()
    assert readme.startswith("Before\n" + START_MARKER)
    assert readme.endswith(END_MARKER + "\nAfter\n")
    button = button_markdown(package_url)
    target = button.rsplit("](", 1)[1][:-1]
    assert parse_qs(urlsplit(target).query)["zipUrl"] == [package_url]
    with ZipFile(archive) as package:
        assert package.read("README.md").decode() == readme


@pytest.mark.parametrize("readme", ["No markers", START_MARKER + START_MARKER + END_MARKER, END_MARKER + START_MARKER])
def test_ambiguous_or_reversed_markers_fail(readme):
    with pytest.raises(ValueError):
        updated_readme(readme, "https://example.test/stack.zip")

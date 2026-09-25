#!/usr/bin/env python3
"""Build reviewed source and Resource Manager artifacts; never upload or deploy.

Only manifest-listed files are eligible. The Resource Manager ZIP preserves
the source layout, so its Terraform working directory is deploy/reference.
Original paths remain available for documentation and CLI usage.
"""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import zipfile


RESOURCE_MANAGER_WORKING_DIRECTORY = "deploy/reference"
IMAGE_DEFAULTS = ("function_image", "function_image_digest", "function_shape")


def image_prefill(args):
    """Validate all-or-nothing, non-secret image values for a generated ZIP."""
    values = {
        "function_image": args.function_image,
        "function_image_digest": args.function_image_digest,
        "function_shape": args.function_shape,
    }
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise SystemExit(
            "--function-image, --function-image-digest and --function-shape must be supplied together"
        )
    if any(character.isspace() for character in values["function_image"]) or "@" in values["function_image"]:
        raise SystemExit("function image must be a non-secret registry address including a tag, not a digest reference")
    repository = values["function_image"].rsplit("/", 1)[-1]
    if ":" not in repository or repository.endswith(":"):
        raise SystemExit("function image must include a review tag; the supplied digest is the immutable pin")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", values["function_image_digest"]):
        raise SystemExit("function image digest must be sha256: followed by 64 lowercase hexadecimal characters")
    if values["function_shape"] not in {"GENERIC_ARM", "GENERIC_X86"}:
        raise SystemExit("function shape must be GENERIC_ARM or GENERIC_X86")
    return values


def set_schema_default(schema, variable, value):
    """Set one known top-level schema variable without reformatting the YAML."""
    expression = re.compile(
        rf"(?ms)^(  {re.escape(variable)}:\n)(.*?)(?=^  [A-Za-z_][A-Za-z0-9_]*:|^outputs:|\Z)"
    )
    match = expression.search(schema)
    if not match:
        raise SystemExit(f"schema.yaml does not define {variable}")
    default = f"    default: {json.dumps(value)}\n"
    body = match.group(2)
    if re.search(r"(?m)^    default:.*$", body):
        body = re.sub(r"(?m)^    default:.*$", default.rstrip("\n"), body)
    else:
        body += default
    return schema[:match.start()] + match.group(1) + body + schema[match.end():]


def prefilled_resource_manager_files(files, values):
    """Return a Resource Manager-only file set with reviewable image defaults."""
    result = dict(files)
    schema = result[f"{RESOURCE_MANAGER_WORKING_DIRECTORY}/schema.yaml"].decode("utf-8")
    schema = set_schema_default(schema, "build_function_image", False)
    for variable in IMAGE_DEFAULTS:
        schema = set_schema_default(schema, variable, values[variable])
    schema_path = f"{RESOURCE_MANAGER_WORKING_DIRECTORY}/schema.yaml"
    result[schema_path] = schema.encode("utf-8")

    # The generated ZIP remains self-verifiable after its schema default is
    # intentionally changed. The generic source tarball is left untouched.
    release_manifest = json.loads(result["RELEASE_MANIFEST.json"])
    schema_entry = next(
        (entry for entry in release_manifest["files"] if entry["path"] == schema_path),
        None,
    )
    if schema_entry is None:
        raise SystemExit("RELEASE_MANIFEST.json does not list schema.yaml")
    schema_entry["bytes"] = len(result[schema_path])
    schema_entry["sha256"] = hashlib.sha256(result[schema_path]).hexdigest()
    result["RELEASE_MANIFEST.json"] = (
        json.dumps(release_manifest, indent=2) + "\n"
    ).encode("utf-8")
    result["IMAGE_PROVENANCE.json"] = (
        json.dumps(
            {
                "function_image": values["function_image"],
                "function_image_digest": values["function_image_digest"],
                "function_shape": values["function_shape"],
                "source_release": release_manifest["release"],
                "note": "Generated deployment defaults. Review these visible values before creating or applying a stack.",
            },
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument(
        "--function-image",
        help="Reviewed OCIR address including tag for a generated prefilled Resource Manager ZIP",
    )
    parser.add_argument("--function-image-digest", help="Immutable sha256 digest for --function-image")
    parser.add_argument("--function-shape", help="GENERIC_ARM or GENERIC_X86 for the reviewed image")
    args = parser.parse_args()
    prefill = image_prefill(args)
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / "RELEASE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    version = manifest["release"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]+", version):
        raise SystemExit("Invalid release name")
    files = {}
    for entry in manifest["files"]:
        name = entry["path"]
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise SystemExit(f"Unsafe source path: {name}")
        if name in files or name == "RELEASE_MANIFEST.json":
            raise SystemExit(f"Duplicate/self-referencing manifest entry: {name}")
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise SystemExit(f"Manifest mismatch: {name}")
        if re.search(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|/p/[A-Za-z0-9_-]{16,}/n/|ocid1\.[a-z]+\.oc1[^\s\"]{45,}", data):
            raise SystemExit(f"Possible credential, PAR or live identifier: {name}")
        files[name] = data
    files["RELEASE_MANIFEST.json"] = manifest_path.read_bytes()
    for name, data in files.items():
        if name.endswith(".md"):
            for link in re.findall(r"\]\(([^)]+)\)", data.decode()):
                if "://" in link or link.startswith("#"):
                    continue
                target = (root / Path(name).parent / link.split("#")[0]).resolve()
                if not target.is_relative_to(root) or target.relative_to(root).as_posix() not in files:
                    raise SystemExit(f"Unpackaged link in {name}: {link}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"oci-pool-controller-{version}"
    tar_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=tar_buffer, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(f"{prefix}/{name}")
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
    zip_files = prefilled_resource_manager_files(files, prefill) if prefill else dict(files)
    zip_files["RESOURCE_MANAGER_MANIFEST.json"] = (json.dumps({
        "release": version,
        "working_directory": RESOURCE_MANAGER_WORKING_DIRECTORY,
        "files": [{"path": n, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for n, b in sorted(zip_files.items())],
    }, indent=2) + "\n").encode()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(zip_files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    zip_name = (
        f"{prefix}-resource-manager-{prefill['function_image_digest'][7:19]}.zip"
        if prefill
        else f"{prefix}-resource-manager.zip"
    )
    artifacts = {f"{prefix}.tar.gz": tar_buffer.getvalue(), zip_name: zip_buffer.getvalue()}
    for name, data in list(artifacts.items()):
        artifacts[name + ".sha256"] = (hashlib.sha256(data).hexdigest() + "  " + name + "\n").encode()
    for name, data in artifacts.items():
        path = args.output_dir / name
        if path.exists() and path.read_bytes() != data:
            raise SystemExit(f"Refusing to replace differing artifact: {path}; use a new release version")
    for name, data in artifacts.items():
        path = args.output_dir / name
        if not path.exists():
            with path.open("xb") as output:
                output.write(data)
        print(path)


if __name__ == "__main__":
    main()

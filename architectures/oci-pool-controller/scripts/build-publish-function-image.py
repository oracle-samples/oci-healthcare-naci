#!/usr/bin/env python3
"""Build, push and pin the controller Function image into a stack-ready ZIP.

Credentials are deliberately not accepted as arguments. Authenticate Podman
using the operator's approved local credential helper or CI secret workflow
before running this command. The script records only the image address, the
digest returned by the successful registry push, and its Function architecture.
It never creates an OCI stack or applies Terraform.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def run(command):
    print("+", " ".join(command))
    subprocess.run(command, check=True)


def write_if_same_or_new(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise SystemExit(f"Refusing to replace differing artifact: {path}")
        return
    with path.open("xb") as output:
        output.write(data)


def validate_image(image):
    if not image or any(character.isspace() for character in image) or "@" in image:
        raise SystemExit("--image must be a non-secret registry address including a tag")
    repository = image.rsplit("/", 1)[-1]
    if ":" not in repository or repository.endswith(":"):
        raise SystemExit("--image must include a review tag; the registry-returned digest will be the immutable pin")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Private OCIR image address including a review tag")
    parser.add_argument("--architecture", choices=("arm64", "amd64"), default="arm64")
    parser.add_argument("--engine", default="podman", help="Podman executable already authenticated to OCIR")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()

    validate_image(args.image)
    engine = shutil.which(args.engine)
    if engine is None:
        raise SystemExit(f"Container engine not found: {args.engine}. Install Podman or pass --engine with its executable.")

    root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    platform = f"linux/{args.architecture}"
    shape = "GENERIC_ARM" if args.architecture == "arm64" else "GENERIC_X86"

    run([
        engine,
        "build",
        "--platform",
        platform,
        "--file",
        str(root / "function" / "Dockerfile"),
        "--tag",
        args.image,
        str(root / "function"),
    ])
    with tempfile.TemporaryDirectory(prefix="oci-pool-controller-digest-") as temporary_directory:
        digest_path = Path(temporary_directory) / "pushed-image.digest"
        run([engine, "push", "--digestfile", str(digest_path), args.image])
        digest = digest_path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit("Podman did not return a valid immutable sha256 digest after push")

    run([
        sys.executable,
        str(root / "scripts" / "package-reference.py"),
        "--output-dir",
        str(output_dir),
        "--function-image",
        args.image,
        "--function-image-digest",
        digest,
        "--function-shape",
        shape,
    ])
    values = {
        "function_image": args.image,
        "function_image_digest": digest,
        "function_shape": shape,
    }
    sidecar_name = f"function-image-values-{digest[7:19]}.json"
    sidecar_path = output_dir / sidecar_name
    write_if_same_or_new(sidecar_path, (json.dumps(values, indent=2) + "\n").encode("utf-8"))
    print(sidecar_path)
    print("Image push succeeded. The generated Resource Manager ZIP pre-fills these values for review; it does not create or apply a stack.")


if __name__ == "__main__":
    main()

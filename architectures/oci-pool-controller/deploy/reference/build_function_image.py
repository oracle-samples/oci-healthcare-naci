#!/usr/bin/env python3
"""Build and push the Function during an OCI Resource Manager apply.

The Terraform caller supplies POOL_IMAGE, POOL_REGISTRY, POOL_OCIR_USERNAME,
POOL_OCIR_AUTH_TOKEN and POOL_FUNCTION_SOURCE_DIR in the environment. Registry
credentials go only to the detected engine's login standard input and temporary
auth files. Supports Docker (including 19.x), native Podman, and podman-docker
wrappers on a native x86 Linux builder; no Buildx or emulation is required.
"""

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


SOURCE_FILES = ("Dockerfile", "func.py", "requirements.txt")
# Preserve only what the engine needs to find its service and reach registries.
# In particular, Terraform variables and OCI credentials must not reach builds.
ENGINE_ENV_KEYS = (
    "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION",
    "XDG_RUNTIME_DIR", "CONTAINER_HOST", "CONTAINER_CONNECTION", "CONTAINER_SSHKEY",
)


class BuildError(Exception):
    """An input, build, or registry operation prevented deployment."""


def required(environment, name):
    value = environment.get(name, "")
    if not value or any(character in value for character in "\x00\r\n"):
        raise BuildError("{} must be provided without control characters".format(name))
    return value


def configuration(environment):
    registry = required(environment, "POOL_REGISTRY")
    image = required(environment, "POOL_IMAGE")
    username = required(environment, "POOL_OCIR_USERNAME")
    token = required(environment, "POOL_OCIR_AUTH_TOKEN")
    source_value = required(environment, "POOL_FUNCTION_SOURCE_DIR")
    hostname = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    if not re.fullmatch(hostname + r"(?:\." + hostname + r")+", registry):
        raise BuildError("POOL_REGISTRY must be a registry hostname without a URL scheme")
    component = r"[a-z0-9][a-z0-9._-]*"
    tagged_path = component + r"(?:/" + component + r")+:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
    if not image.startswith(registry + "/") or not re.fullmatch(
        tagged_path, image[len(registry) + 1:]
    ):
        raise BuildError("POOL_IMAGE must name this registry, namespace, repository and tag")
    if username.startswith("-") or any(character.isspace() for character in username):
        raise BuildError("POOL_OCIR_USERNAME must be a complete OCIR username without whitespace")
    try:
        source = Path(source_value).resolve(strict=True)
    except (OSError, RuntimeError):
        raise BuildError("POOL_FUNCTION_SOURCE_DIR does not resolve to an existing directory")
    if not source.is_dir():
        raise BuildError("POOL_FUNCTION_SOURCE_DIR must be a directory")
    for filename in SOURCE_FILES:
        path = source / filename
        if path.is_symlink() or not path.is_file():
            raise BuildError("Function source must contain a regular {} file".format(filename))
    return registry, image, username, token, source


def build_and_push(environment=None):
    environment = os.environ if environment is None else environment
    registry, image, username, token, source = configuration(environment)
    engine_environment = {
        key: environment[key] for key in ENGINE_ENV_KEYS
        if key in environment and token not in environment[key]
    }
    executable = next((name for name in ("docker", "podman")
                       if shutil.which(name, path=engine_environment.get("PATH", os.defpath))), None)
    if executable is None:
        raise BuildError("Automatic builds require Docker or Podman on a native linux/amd64 builder")
    secrets = (token, base64.b64encode((username + ":" + token).encode()).decode())

    def redact(value):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
        return value

    with tempfile.TemporaryDirectory(prefix="oci-pool-function-build-") as temporary:
        root = Path(temporary)
        config = root / "docker-config"
        authfile = root / "registry-auth.json"
        context = root / "context"
        config.mkdir(mode=0o700)
        context.mkdir(mode=0o700)
        for path in (config / "config.json", authfile):
            path.write_text('{"auths": {}}\n', encoding="utf-8")
            path.chmod(0o600)
        # Never inherit the operator's registry credentials or credential helpers.
        # Set these even for the detection probe, before we know the engine.
        engine_environment["DOCKER_CONFIG"] = str(config)
        engine_environment["REGISTRY_AUTH_FILE"] = str(authfile)
        for filename in SOURCE_FILES:
            shutil.copyfile(source / filename, context / filename)

        def run(arguments, stdin=None, display=True):
            command = [executable] + arguments
            try:
                result = subprocess.run(
                    command, input=stdin, universal_newlines=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=engine_environment, check=False,
                )
            except OSError as error:
                raise BuildError("Unable to start {}: {}".format(executable, redact(str(error))))
            stdout = redact(result.stdout or "")
            stderr = redact(result.stderr or "")
            if result.returncode:
                raise BuildError("{} command failed (exit {}): {}".format(
                    executable, result.returncode, (stderr or stdout).strip()
                ))
            if display:
                if stdout:
                    print(stdout, end="" if stdout.endswith("\n") else "\n")
                if stderr:
                    print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
            return stdout.strip()

        def json_result(arguments):
            try:
                return json.loads(run(arguments, display=False))
            except ValueError:
                raise BuildError("{} returned invalid JSON; image build/push cancelled".format(executable))

        # Both engines support serializing the complete info object, but Podman
        # has host.os/host.arch, NOT Docker's OSType/Architecture template fields.
        # Inspect the response, not the executable name: docker may be a shim.
        info = json_result(["info", "--format", "{{json .}}"])
        if not isinstance(info, dict):
            raise BuildError("Unrecognized container-engine info; image build/push cancelled")
        host = info.get("host", info.get("Host"))
        if isinstance(host, dict):
            engine = "podman"
            operating_system = host.get("os", host.get("OS", host.get("Os")))
            architecture = host.get("arch", host.get("Arch"))
        elif "OSType" in info and "Architecture" in info:
            engine = "docker"
            operating_system, architecture = info["OSType"], info["Architecture"]
        else:
            raise BuildError("Unrecognized container-engine info; image build/push cancelled")
        if operating_system != "linux" or architecture not in ("amd64", "x86_64"):
            raise BuildError("Detected {} via {} reports platform {}/{}. The automatic Function "
                             "build requires a native linux/amd64 builder; use a native x86 "
                             "runner or supply a reviewed prebuilt image".format(
                                 engine, executable, operating_system, architecture))

        def container(arguments, **kwargs):
            if engine == "docker":
                arguments = ["--config", str(config)] + arguments
            elif arguments[0] in ("build", "login", "push"):
                arguments = arguments[:1] + ["--authfile", str(authfile)] + arguments[1:]
            return run(arguments, **kwargs)

        print("Detected {} via {}; building Function image for linux/amd64.".format(engine, executable), flush=True)
        # Docker 19 gates --platform behind experimental/BuildKit support.
        # The native daemon check above and image check below enforce x86
        # without requiring those optional features on the RM worker.
        platform = ["--platform", "linux/amd64"] if engine == "podman" else []
        container(["build"] + platform + [
            "--file", str(context / "Dockerfile"), "--tag", image, str(context),
        ])
        try:
            inspected = json.loads(container(["image", "inspect", image], display=False))
        except ValueError:
            raise BuildError("Image inspection returned invalid JSON; registry push was cancelled")
        if (not isinstance(inspected, list) or len(inspected) != 1
                or not isinstance(inspected[0], dict)
                or inspected[0].get("Os") != "linux"
                or inspected[0].get("Architecture") != "amd64"):
            raise BuildError("Built Function image is not linux/amd64; registry push was cancelled")
        print("Verified built Function image: linux/amd64 (GENERIC_X86).", flush=True)
        container(["login", "--username", username, "--password-stdin", registry], stdin=token + "\n")
        container(["push", image])
        print("Function image pushed successfully; OCI Functions will resolve its immutable digest.")


def main():
    try:
        build_and_push()
    except (BuildError, OSError) as error:
        # Also redact errors originating from filesystem operations.
        message = str(error)
        token = os.environ.get("POOL_OCIR_AUTH_TOKEN")
        if token:
            message = message.replace(token, "[redacted]")
        print("Function image build failed: {}".format(message), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

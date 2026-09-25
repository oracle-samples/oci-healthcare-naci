#!/usr/bin/env python3
"""DevOps-only, credential-free native x86 build with source/output checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

FILES = ("Dockerfile", "func.py", "requirements.txt")
IMAGE = "pool-controller:build"
ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
            "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "XDG_RUNTIME_DIR")


def build(root=None, environment=None):
    root = Path.cwd() if root is None else Path(root)
    environment = os.environ if environment is None else environment
    expected = json.loads(environment["POOL_SOURCE_SHA256"])
    if not isinstance(expected, dict) or set(expected) != set(FILES):
        raise ValueError("Expected checksums for exactly the three Function source files")
    for name in FILES:
        path = root / "function" / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]:
            raise ValueError("Source checksum/regular-file verification failed: " + name)
    print("Verified exact packaged Function source checksums.", flush=True)
    env = {key: environment[key] for key in ENV_KEYS if key in environment}
    executable = next((name for name in ("podman", "docker")
                       if shutil.which(name, path=env.get("PATH", os.defpath))), None)
    if executable is None:
        raise RuntimeError("Native build requires Podman or Docker")
    with tempfile.TemporaryDirectory(prefix="pool-native-build-") as temporary:
        directory = Path(temporary)
        context = directory / "context"
        context.mkdir(mode=0o700)
        config = directory / "docker-config"
        config.mkdir(mode=0o700)
        authfile = directory / "auth.json"
        for path in (config / "config.json", authfile):
            path.write_text('{"auths": {}}\n')
            path.chmod(0o600)
        env.update(DOCKER_CONFIG=str(config), REGISTRY_AUTH_FILE=str(authfile))
        for name in FILES:
            shutil.copyfile(root / "function" / name, context / name)

        def run(args, display=False):
            result = subprocess.run([executable] + args, env=env, check=False,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    universal_newlines=True, timeout=1400)
            if result.returncode:
                raise RuntimeError("Container command failed: " + (result.stderr or result.stdout).strip())
            if display:
                print(result.stdout, flush=True)
                print(result.stderr, flush=True)
            return result.stdout

        info = json.loads(run(["info", "--format", "{{json .}}"]))
        host = info.get("host", info.get("Host"))
        podman = isinstance(host, dict)
        operating_system = host.get("os", host.get("OS", host.get("Os"))) if podman else info.get("OSType")
        architecture = host.get("arch", host.get("Arch")) if podman else info.get("Architecture")
        if operating_system != "linux" or architecture not in ("amd64", "x86_64"):
            raise RuntimeError("Native x86 required; engine reports {}/{}".format(operating_system, architecture))
        print("Verified native builder: linux/amd64; engine=" + ("podman" if podman else "docker"), flush=True)
        command = (["build", "--authfile", str(authfile), "--platform", "linux/amd64", "--format", "docker"]
                   if podman else ["--config", str(config), "build"])
        run(command + ["--file", str(context / "Dockerfile"), "--tag", IMAGE, str(context)], display=True)
        prefix = [] if podman else ["--config", str(config)]
        result = json.loads(run(prefix + ["image", "inspect", IMAGE]))
        if (not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict)
                or result[0].get("Os") != "linux" or result[0].get("Architecture") != "amd64"):
            raise RuntimeError("Output image is not linux/amd64; delivery is blocked")
        print("Verified output image: linux/amd64 (GENERIC_X86). Delivery uses the pipeline resource principal.", flush=True)


if __name__ == "__main__":
    build()

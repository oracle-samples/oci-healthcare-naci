#!/usr/bin/env python3
"""Publish only packaged build inputs to a new branch of the stack's OCI repo.

Credentials live in a private temporary directory, scoped to the exact HTTPS
repository. No shell interpolation, global Git changes, force push or token in
Git history, arguments, URLs or build runner environment.
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
from urllib.parse import urlsplit

FUNCTION_FILES = ("Dockerfile", "func.py", "requirements.txt")
BUILD_FILES = ("build_spec.yaml", "native_build.py")
ENV_KEYS = ("PATH", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")

HELPER = '''import json, pathlib, sys
if len(sys.argv) != 2 or sys.argv[1] != "get":
    sys.exit(0)
values = dict(line.rstrip("\\n").split("=", 1) for line in sys.stdin if "=" in line)
record = json.loads(pathlib.Path(__file__).with_name("credential.json").read_text())
if all(values.get(key) == record[key] for key in ("protocol", "host", "path")):
    print("username=" + record["username"])
    print("password=" + record["password"])
'''


def publish(environment=None, module=None):
    environment = os.environ if environment is None else environment
    module = Path(__file__).resolve().parent if module is None else Path(module)
    names = ("POOL_SOURCE_REPOSITORY", "POOL_SOURCE_BRANCH", "POOL_SOURCE_USERNAME",
             "POOL_SOURCE_AUTH_TOKEN", "POOL_FUNCTION_SOURCE_DIR")
    values = []
    for name in names:
        value = environment.get(name, "")
        if not value or any(c in value for c in "\x00\r\n"):
            raise ValueError(name + " must be provided without control characters")
        values.append(value)
    url, branch, username, token, source = values
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or parsed.query or parsed.fragment or not re.fullmatch(
                r"(?:[a-z0-9-]+\.devops|devops\.scmservice\.[a-z0-9-]+)\.oci\.oraclecloud\.com", parsed.hostname or "")
            or not re.fullmatch(r"/namespaces/[^/]+/projects/[^/]+/repositories/[^/]+", parsed.path)):
        raise ValueError("Source target must be the stack's HTTPS OCI DevOps code repository")
    if not re.fullmatch(r"build-[a-f0-9-]{8,64}", branch):
        raise ValueError("Source branch must be a unique build- identifier")
    source = Path(source).resolve(strict=True)
    files = {"function/" + name: source / name for name in FUNCTION_FILES}
    files.update({name: module / name for name in BUILD_FILES})
    for name, path in files.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Build input must be a regular file: " + name)
    secrets = (token, base64.b64encode((username + ":" + token).encode()).decode())

    def redact(text):
        for secret in secrets:
            text = text.replace(secret, "[redacted]")
        return text

    with tempfile.TemporaryDirectory(prefix="pool-build-source-") as temporary:
        root = Path(temporary)
        credential = root / "credential.json"
        credential.write_text(json.dumps({"protocol": "https", "host": parsed.netloc,
                                          "path": parsed.path.lstrip("/"),
                                          "username": username, "password": token}))
        credential.chmod(0o600)
        helper = root / "credential.py"
        helper.write_text(HELPER)
        helper.chmod(0o600)
        git_env = {key: environment[key] for key in ENV_KEYS if key in environment and token not in environment[key]}
        git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        # Use a git credential helper, not an HTTP header or credential in argv.
        import shlex
        prefix = ["git", "-c", "credential.helper=", "-c", "credential.useHttpPath=true",
                  "-c", "credential.helper=!" + shlex.quote(sys.executable) + " " + shlex.quote(str(helper)),
                  "-c", "http.followRedirects=false", "-c", "core.hooksPath=" + os.devnull,
                  "-c", "protocol.file.allow=never"]
        def git(arguments, cwd=root):
            result = subprocess.run(prefix + arguments, cwd=str(cwd), env=git_env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    universal_newlines=True, timeout=180, check=False)
            if result.returncode:
                raise RuntimeError("Source publication failed: " + redact(result.stderr or result.stdout))
            return result.stdout.strip()
        checkout = root / "source"
        git(["clone", "--no-checkout", "--", url, str(checkout)])
        # Preserve the service's initial commit. A fresh branch is never forced.
        git(["checkout", "-b", branch], checkout)
        for name, path in files.items():
            target = checkout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        git(["add", "--"] + sorted(files), checkout)
        git(["-c", "user.name=OCI Pool Controller Build", "-c", "user.email=build@example.invalid",
             "commit", "-m", "Publish reviewed controller build inputs"], checkout)
        git(["push", "origin", "HEAD:refs/heads/" + branch], checkout)
        print("Published allowlisted build inputs on " + branch + " at " + git(["rev-parse", "HEAD"], checkout))


if __name__ == "__main__":
    try:
        publish()
    except Exception as error:
        token = os.environ.get("POOL_SOURCE_AUTH_TOKEN", "")
        message = str(error).replace(token, "[redacted]") if token else str(error)
        print("Build source publication failed: " + message, file=sys.stderr)
        sys.exit(1)

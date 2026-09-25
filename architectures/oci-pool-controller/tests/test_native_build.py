"""Native DevOps build and source publication security regression tests."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy/reference" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = load("native_build")
publisher = load("publish_build_source")


class NativeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "function").mkdir()
        self.hashes = {}
        for name in native.FILES:
            content = ("fixture " + name).encode()
            (self.root / "function" / name).write_bytes(content)
            self.hashes[name] = hashlib.sha256(content).hexdigest()
        (self.root / "function/.env").write_text("SECRET_DO_NOT_COPY")
        self.env = {"PATH": "/usr/bin:/bin", "POOL_SOURCE_SHA256": json.dumps(self.hashes),
                    "OCI_RESOURCE_PRINCIPAL_RPST": "secret-rpst", "POOL_SOURCE_AUTH_TOKEN": "secret-token",
                    "DOCKER_DEFAULT_PLATFORM": "linux/arm64"}
        self.calls = []
        self.contexts = []
        self.info = {"host": {"os": "linux", "arch": "amd64"}}
        self.image = [{"Os": "linux", "Architecture": "amd64"}]
        self.fail_build = False

    def fake(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.assertNotIn("secret", repr(kwargs["env"]))
        self.assertNotIn("OCI_RESOURCE_PRINCIPAL_RPST", kwargs["env"])
        self.assertNotIn("DOCKER_DEFAULT_PLATFORM", kwargs["env"])
        self.assertNotIn("shell", kwargs)
        if "info" in command:
            output = json.dumps(self.info)
        elif "build" in command:
            context = Path(command[-1])
            self.contexts.append(context)
            self.assertEqual(set(p.name for p in context.iterdir()), set(native.FILES))
            if self.fail_build:
                return subprocess.CompletedProcess(command, 1, "", "build failed")
            output = "built"
        else:
            self.assertIn("inspect", command)
            output = json.dumps(self.image)
        return subprocess.CompletedProcess(command, 0, output, "")

    def run_build(self, binary="podman"):
        with mock.patch.object(native.shutil, "which", side_effect=lambda name, **kw: name if name == binary else None), \
             mock.patch.object(native.subprocess, "run", side_effect=self.fake), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            native.build(self.root, self.env)
            return output.getvalue()

    def test_native_podman_builds_only_verified_x86_without_credentials(self):
        output = self.run_build()
        self.assertIn("Verified output image: linux/amd64", output)
        build = next(command for command, _ in self.calls if "build" in command)
        self.assertIn("--platform", build)
        self.assertIn("linux/amd64", build)
        self.assertIn("--authfile", build)
        self.assertIn("--format", build)
        self.assertFalse(any(path.exists() for path in self.contexts))
        self.assertFalse(any("login" in command or "push" in command for command, _ in self.calls))

    def test_docker19_and_podman_docker_wrapper(self):
        for info in ({"OSType": "linux", "Architecture": "x86_64"},
                     {"Host": {"Os": "linux", "Arch": "amd64"}}):
            with self.subTest(info=info):
                self.info = info
                self.calls.clear()
                self.run_build("docker")
                build = next(command for command, _ in self.calls if "build" in command)
                self.assertEqual("--config" in build, "OSType" in info)

    def test_arm_host_fails_before_build(self):
        self.info["host"]["arch"] = "arm64"
        with self.assertRaisesRegex(RuntimeError, "Native x86 required"):
            self.run_build()
        self.assertEqual(len(self.calls), 1)

    def test_wrong_image_or_build_failure_blocks_delivery(self):
        for image, fail in [([{"Os": "linux", "Architecture": "arm64"}], False), ([], False), (None, True)]:
            with self.subTest(image=image, fail=fail):
                self.image, self.fail_build = image, fail
                with self.assertRaises(RuntimeError):
                    self.run_build()
                self.assertFalse(any(path.exists() for path in self.contexts))

    def test_source_checksum_mismatch_missing_or_extra_fails_before_engine(self):
        for hashes in ({}, {**self.hashes, "secret": "a"}, {**self.hashes, "func.py": "0" * 64}):
            with self.subTest(hashes=hashes):
                self.env["POOL_SOURCE_SHA256"] = json.dumps(hashes)
                with self.assertRaises(ValueError):
                    self.run_build()
        self.assertEqual(self.calls, [])

    def test_symlink_source_is_rejected(self):
        path = self.root / "function/func.py"
        path.unlink()
        path.symlink_to(self.root / "function/Dockerfile")
        with self.assertRaises(ValueError):
            self.run_build()
        self.assertEqual(self.calls, [])


class SourcePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "function"
        self.source.mkdir()
        self.module = self.root / "module"
        self.module.mkdir()
        for name in publisher.FUNCTION_FILES:
            (self.source / name).write_text("fixture " + name)
        for name in publisher.BUILD_FILES:
            (self.module / name).write_text("fixture " + name)
        (self.source / ".env").write_text("do not copy")
        self.token = "test-only-secret-token"
        self.url = "https://devops.scmservice.us-ashburn-1.oci.oraclecloud.com/namespaces/example/projects/build/repositories/source"
        self.env = {"PATH": "/usr/bin:/bin", "POOL_SOURCE_REPOSITORY": self.url,
                    "POOL_SOURCE_BRANCH": "build-12345678-abcd", "POOL_SOURCE_USERNAME": "tenancy/Default/test",
                    "POOL_SOURCE_AUTH_TOKEN": self.token, "POOL_FUNCTION_SOURCE_DIR": str(self.source),
                    "TF_VAR_secret": self.token, "GIT_CONFIG_COUNT": "1", "GIT_TRACE_CURL": "1"}
        self.calls = []
        self.auth_paths = []

    def fake(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.assertNotIn(self.token, repr(command) + repr(kwargs["env"]))
        self.assertNotIn("GIT_TRACE_CURL", kwargs["env"])
        self.assertNotIn("GIT_CONFIG_COUNT", kwargs["env"])
        self.assertIn("credential.helper=", command)
        self.assertIn("http.followRedirects=false", command)
        self.assertNotIn("--force", command)
        self.assertNotIn("shell", kwargs)
        if "clone" in command:
            root = Path(kwargs["cwd"])
            credential = root / "credential.json"
            self.auth_paths.append(credential)
            self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(credential.read_text())["password"], self.token)
            Path(command[-1]).mkdir()
        if "add" in command:
            checkout = Path(kwargs["cwd"])
            self.assertEqual(set(p.relative_to(checkout).as_posix() for p in checkout.rglob("*") if p.is_file()),
                             {"function/" + n for n in publisher.FUNCTION_FILES} | set(publisher.BUILD_FILES))
        return subprocess.CompletedProcess(command, 0, "a" * 40 if "rev-parse" in command else "", "")

    def run_publish(self, side_effect=None):
        with mock.patch.object(publisher.subprocess, "run", side_effect=side_effect or self.fake), \
             contextlib.redirect_stdout(io.StringIO()):
            publisher.publish(self.env, self.module)

    def test_allowlisted_publication_and_private_credentials_cleanup(self):
        self.run_publish()
        self.assertEqual(len(self.calls), 6)
        self.assertFalse(any(path.exists() for path in self.auth_paths))
        self.assertIn("HEAD:refs/heads/build-12345678-abcd", self.calls[-2][0])

    def test_no_external_credential_destination(self):
        for url in ["http://example.com/repo", self.url.replace("oraclecloud.com", "example.com"),
                    self.url + "?x=1", self.url.replace("https://", "https://user:pass@"),
                    self.url.replace("/namespaces/", "/other/")]:
            with self.subTest(url=url):
                self.env["POOL_SOURCE_REPOSITORY"] = url
                with self.assertRaises(ValueError):
                    self.run_publish()
        self.assertEqual(self.calls, [])

    def test_rejects_untrusted_branch_and_source_symlink(self):
        for branch in ["main", "build-x;bad", "build-12345678\n"]:
            self.env["POOL_SOURCE_BRANCH"] = branch
            with self.assertRaises(ValueError):
                self.run_publish()
        self.env["POOL_SOURCE_BRANCH"] = "build-12345678"
        path = self.source / "func.py"
        path.unlink()
        path.symlink_to(self.source / "Dockerfile")
        with self.assertRaises(ValueError):
            self.run_publish()

    def test_failure_redacts_raw_and_encoded_token_and_cleans_up(self):
        encoded = base64.b64encode((self.env["POOL_SOURCE_USERNAME"] + ":" + self.token).encode()).decode()
        def failure(command, **kwargs):
            result = self.fake(command, **kwargs)
            result.returncode, result.stderr = 1, self.token + " " + encoded
            return result
        with self.assertRaises(RuntimeError) as raised:
            self.run_publish(failure)
        self.assertNotIn(self.token, str(raised.exception))
        self.assertNotIn(encoded, str(raised.exception))
        self.assertFalse(any(path.exists() for path in self.auth_paths))

    def test_credential_helper_only_answers_exact_repository_get(self):
        helper = self.root / "credential.py"
        helper.write_text(publisher.HELPER)
        record = {"protocol": "https", "host": "example.oci.oraclecloud.com", "path": "namespaces/only",
                  "username": "example", "password": "fixture-token"}
        (self.root / "credential.json").write_text(json.dumps(record))
        for operation, path in [("get", record["path"]), ("get", "namespaces/other"), ("store", record["path"])]:
            request = "protocol=https\nhost=example.oci.oraclecloud.com\npath=" + path + "\n\n"
            result = subprocess.run([sys.executable, str(helper), operation], input=request,
                                    universal_newlines=True, capture_output=True, check=True)
            self.assertEqual("fixture-token" in result.stdout, operation == "get" and path == record["path"])


if __name__ == "__main__":
    unittest.main()

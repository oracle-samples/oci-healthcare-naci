"""Verify Docker/Podman image-build boundaries without an OCI tenancy."""

import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/reference/build_function_image.py"
SPEC = importlib.util.spec_from_file_location("resource_manager_image_build", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ImageBuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name)
        for filename in builder.SOURCE_FILES:
            (self.source / filename).write_text("test fixture\n", encoding="utf-8")
        (self.source / ".env").write_text("DO_NOT_COPY=secret\n", encoding="utf-8")
        (self.source / "untracked.txt").write_text("not Function source\n", encoding="utf-8")
        self.token = "unit-test-registry-token"
        self.environment = {
            "POOL_IMAGE": "iad.ocir.io/testnamespace/controller:source-123",
            "POOL_REGISTRY": "iad.ocir.io",
            "POOL_OCIR_USERNAME": "testnamespace/default/operator@example.com",
            "POOL_OCIR_AUTH_TOKEN": self.token,
            "POOL_FUNCTION_SOURCE_DIR": str(self.source),
            "PATH": "/usr/bin:/bin",
            "TF_VAR_ocir_auth_token": self.token,
            "OCI_CLI_KEY_CONTENT": "another-secret",
            "DOCKER_CONFIG": "/operator/existing/docker-config",
            "REGISTRY_AUTH_FILE": "/operator/existing/auth.json",
            "DOCKER_DEFAULT_PLATFORM": "linux/arm64",
        }
        self.engine = "docker"
        self.binary = "docker"
        self.calls = []
        self.temporary_paths = set()

    def step(self, command):
        return command[3] if command[1] == "--config" else command[1]

    def steps(self):
        return [self.step(command) for command, _ in self.calls]

    def fake_docker(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.assertEqual(command[0], self.binary)
        config = Path(kwargs["env"]["DOCKER_CONFIG"])
        authfile = Path(kwargs["env"]["REGISTRY_AUTH_FILE"])
        self.temporary_paths.add(config.parent)
        self.assertTrue(config.is_dir())
        self.assertEqual(config.stat().st_mode & 0o777, 0o700)
        self.assertEqual(authfile.parent, config.parent)
        self.assertEqual(authfile.stat().st_mode & 0o777, 0o600)
        self.assertEqual((config / "config.json").stat().st_mode & 0o777, 0o600)
        step = self.step(command)
        if step == "info":
            self.assertEqual(command[1:], ["info", "--format", "{{json .}}"])
            self.assertEqual(json.loads(authfile.read_text()), {"auths": {}})
            self.assertEqual(json.loads((config / "config.json").read_text()), {"auths": {}})
        elif self.engine == "docker":
            self.assertEqual(command[1:3], ["--config", str(config)])
            self.assertNotIn("--authfile", command)
        else:
            self.assertNotIn("--config", command)
            if step in ("build", "login", "push"):
                self.assertEqual(command[2:4], ["--authfile", str(authfile)])
        if step == "build":
            context = Path(command[-1])
            self.assertEqual(set(path.name for path in context.iterdir()), set(builder.SOURCE_FILES))
            for filename in builder.SOURCE_FILES:
                self.assertEqual((context / filename).read_bytes(), (self.source / filename).read_bytes())
            if self.engine == "podman":
                self.assertEqual(command[command.index("--platform") + 1], "linux/amd64")
            else:
                self.assertNotIn("--platform", command)  # Docker 19 compatibility.
            self.assertNotIn("buildx", command)
        if step == "login":
            credential_file = authfile if self.engine == "podman" else config / "config.json"
            credential_file.write_text(self.token, encoding="utf-8")
        info = {"OSType": "linux", "Architecture": "x86_64"} if self.engine == "docker" else {
            "host": {"os": "linux", "arch": "amd64"}, "version": {"Version": "4.9.4"},
        }
        output = {"info": json.dumps(info), "image": json.dumps([
            {"Os": "linux", "Architecture": "amd64"}
        ]), "push": "digest: sha256:test\n"}.get(step, "")
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    def run_build(self, side_effect=None):
        with mock.patch.object(builder.shutil, "which", side_effect=lambda name, **kw: (
                "/usr/bin/" + name if name == self.binary else None)):
            with mock.patch.object(builder.subprocess, "run", side_effect=side_effect or self.fake_docker):
                with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                    builder.build_and_push(self.environment)
                    return output.getvalue()

    def assert_cleaned_up(self):
        self.assertTrue(self.temporary_paths)
        for path in self.temporary_paths:
            self.assertFalse(path.exists())

    def test_credentials_are_stdin_only_and_context_is_allowlisted(self):
        self.run_build()
        self.assertEqual(self.steps(), ["info", "build", "image", "login", "push"])
        for command, kwargs in self.calls:
            self.assertNotIn(self.token, " ".join(command))
            self.assertEqual(set(kwargs["env"]), {"PATH", "DOCKER_CONFIG", "REGISTRY_AUTH_FILE"})
            self.assertNotIn(self.token, repr(kwargs["env"]))
            self.assertNotIn("/operator/existing", repr(kwargs["env"]))
            self.assertNotIn("shell", kwargs)
            self.assertIs(kwargs["universal_newlines"], True)
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.PIPE)
            self.assertEqual(kwargs["input"], self.token + "\n" if self.step(command) == "login" else None)
        self.assert_cleaned_up()

    def test_non_x86_daemon_fails_before_build_or_login(self):
        def arm_daemon(command, **kwargs):
            result = self.fake_docker(command, **kwargs)
            result.stdout = json.dumps({"OSType": "linux", "Architecture": "aarch64"})
            return result
        with self.assertRaisesRegex(builder.BuildError, "native linux/amd64"):
            self.run_build(arm_daemon)
        self.assertEqual(self.steps(), ["info"])
        self.assert_cleaned_up()

    def test_wrong_image_architecture_prevents_login_and_push(self):
        def wrong_image(command, **kwargs):
            result = self.fake_docker(command, **kwargs)
            if self.step(command) == "image":
                result.stdout = json.dumps([{"Os": "linux", "Architecture": "arm64"}])
            return result
        with self.assertRaisesRegex(builder.BuildError, "not linux/amd64"):
            self.run_build(wrong_image)
        self.assertEqual(self.steps(), ["info", "build", "image"])
        self.assert_cleaned_up()

    def test_push_failure_cleans_credentials_and_redacts_output(self):
        def failed_push(command, **kwargs):
            result = self.fake_docker(command, **kwargs)
            if self.step(command) == "push":
                result.returncode = 1
                result.stderr = "registry rejected " + self.token
            return result
        with self.assertRaises(builder.BuildError) as raised:
            self.run_build(failed_push)
        self.assertIn("docker command failed", str(raised.exception))
        self.assertNotIn(self.token, str(raised.exception))
        self.assertIn("[redacted]", str(raised.exception))
        self.assert_cleaned_up()

    def test_failed_build_prevents_login_and_push(self):
        def failed_build(command, **kwargs):
            result = self.fake_docker(command, **kwargs)
            if self.step(command) == "build":
                result.returncode = 1
                result.stderr = "Dockerfile failed"
            return result
        with self.assertRaisesRegex(builder.BuildError, "docker command failed"):
            self.run_build(failed_build)
        self.assertEqual(self.steps(), ["info", "build"])
        self.assert_cleaned_up()

    def test_native_podman_and_docker_shim_use_private_authfile(self):
        for binary in ("podman", "docker"):
            with self.subTest(binary=binary):
                self.engine, self.binary = "podman", binary
                self.calls.clear()
                output = self.run_build()
                self.assertIn("Detected podman via " + binary, output)
                self.assertIn("Verified built Function image: linux/amd64 (GENERIC_X86)", output)
                self.assertEqual(self.steps(), ["info", "build", "image", "login", "push"])
                for command, kwargs in self.calls:
                    self.assertNotIn("--config", command)
                    self.assertNotIn(self.token, repr(command) + repr(kwargs["env"]))
                    self.assertEqual(kwargs["input"], self.token + "\n" if self.step(command) == "login" else None)
                self.assert_cleaned_up()

    def test_podman_arm_host_or_image_cannot_be_pushed(self):
        self.engine, self.binary = "podman", "podman"
        for failing_step in ("info", "image"):
            with self.subTest(step=failing_step):
                self.calls.clear()
                def arm(command, **kwargs):
                    result = self.fake_docker(command, **kwargs)
                    if self.step(command) == failing_step:
                        result.stdout = json.dumps({"host": {"os": "linux", "arch": "arm64"}} if
                                                   failing_step == "info" else [{"Os": "linux", "Architecture": "arm64"}])
                    return result
                with self.assertRaises(builder.BuildError):
                    self.run_build(arm)
                self.assertNotIn("login", self.steps())
                self.assertNotIn("push", self.steps())
                self.assert_cleaned_up()

    def test_supported_engine_info_variants(self):
        cases = (
            ("docker", {"OSType": "linux", "Architecture": "amd64"}),
            ("podman", {"Host": {"OS": "linux", "Arch": "amd64"}}),
            ("podman", {"Host": {"Os": "linux", "Arch": "amd64"}}),
            ("podman", {"host": {"os": "linux", "arch": "x86_64"}}),
        )
        for engine, info in cases:
            with self.subTest(engine=engine, info=info):
                self.engine = engine
                self.calls.clear()
                def variant(command, **kwargs):
                    result = self.fake_docker(command, **kwargs)
                    if self.step(command) == "info":
                        result.stdout = json.dumps(info)
                    return result
                self.run_build(variant)
                self.assertEqual(self.steps()[-1], "push")
                self.assert_cleaned_up()

    def test_resource_manager_arm_docker_shim_reports_actual_platform(self):
        self.engine, self.binary = "podman", "docker"
        def arm_shim(command, **kwargs):
            result = self.fake_docker(command, **kwargs)
            result.stdout = json.dumps({"host": {"os": "linux", "arch": "arm64"}})
            return result
        with self.assertRaisesRegex(builder.BuildError,
                                    "Detected podman via docker reports platform linux/arm64"):
            self.run_build(arm_shim)
        self.assertEqual(self.steps(), ["info"])
        self.assert_cleaned_up()

    def test_non_linux_engine_cannot_build_or_authenticate(self):
        def windows(command, **kwargs):
            result = self.fake_docker(command, **kwargs)
            result.stdout = json.dumps({"OSType": "windows", "Architecture": "amd64"})
            return result
        with self.assertRaisesRegex(builder.BuildError, "native linux/amd64"):
            self.run_build(windows)
        self.assertEqual(self.steps(), ["info"])
        self.assert_cleaned_up()

    def test_malformed_or_unknown_engine_and_image_info_fail_closed(self):
        for step, response in (("info", "not json"), ("info", "[]"), ("info", "{}"),
                               ("info", '{"host": null}'), ("image", "not json"),
                               ("image", "{}"), ("image", "[]"), ("image", "[null]")):
            with self.subTest(step=step, response=response):
                self.calls.clear()
                def invalid(command, **kwargs):
                    result = self.fake_docker(command, **kwargs)
                    if self.step(command) == step:
                        result.stdout = response
                    return result
                with self.assertRaises(builder.BuildError):
                    self.run_build(invalid)
                self.assertNotIn("login", self.steps())
                self.assertNotIn("push", self.steps())
                self.assert_cleaned_up()

    def test_podman_failures_redact_credentials_and_always_clean_up(self):
        self.engine, self.binary = "podman", "docker"
        encoded = base64.b64encode((self.environment["POOL_OCIR_USERNAME"] + ":" + self.token).encode()).decode()
        for step in ("info", "build", "image", "login", "push"):
            with self.subTest(step=step):
                self.calls.clear()
                def fail(command, **kwargs):
                    result = self.fake_docker(command, **kwargs)
                    if self.step(command) == step:
                        result.returncode = 1
                        result.stderr = "rejected " + self.token + " " + encoded
                    return result
                with self.assertRaises(builder.BuildError) as raised:
                    self.run_build(fail)
                self.assertNotIn(self.token, str(raised.exception))
                self.assertNotIn(encoded, str(raised.exception))
                self.assertIn("[redacted]", str(raised.exception))
                self.assertEqual(self.steps()[-1], step)
                self.assert_cleaned_up()

    def test_missing_engine_fails_before_subprocess(self):
        with mock.patch.object(builder.shutil, "which", return_value=None), mock.patch.object(builder.subprocess, "run") as run:
            with self.assertRaisesRegex(builder.BuildError, "Docker or Podman"):
                builder.build_and_push(self.environment)
            run.assert_not_called()

    def test_unavailable_engine_does_not_fallback_or_expose_token(self):
        def unavailable(command, **kwargs):
            self.fake_docker(command, **kwargs)
            raise OSError("connection failed " + self.token)
        with self.assertRaises(builder.BuildError) as raised:
            self.run_build(unavailable)
        self.assertNotIn(self.token, str(raised.exception))
        self.assertEqual(self.steps(), ["info"])
        self.assert_cleaned_up()

    def test_image_and_registry_reject_options_or_unmatched_targets(self):
        invalid = (
            ("POOL_IMAGE", "--help"),
            ("POOL_IMAGE", "other.ocir.io/testnamespace/controller:tag"),
            ("POOL_IMAGE", "iad.ocir.io/testnamespace/controller"),
            ("POOL_IMAGE", "iad.ocir.io/testnamespace/controller:tag\n"),
            ("POOL_REGISTRY", "https://iad.ocir.io"),
            ("POOL_REGISTRY", "--help"),
            ("POOL_OCIR_USERNAME", "--help"),
        )
        for name, value in invalid:
            with self.subTest(name=name, value=value):
                environment = dict(self.environment, **{name: value})
                with mock.patch.object(builder.subprocess, "run") as run:
                    with self.assertRaises(builder.BuildError):
                        builder.build_and_push(environment)
                    run.assert_not_called()

    def test_missing_credentials_fail_before_docker(self):
        del self.environment["POOL_OCIR_AUTH_TOKEN"]
        with mock.patch.object(builder.subprocess, "run") as run:
            with self.assertRaisesRegex(builder.BuildError, "POOL_OCIR_AUTH_TOKEN"):
                builder.build_and_push(self.environment)
            run.assert_not_called()

    def test_source_symlink_is_rejected(self):
        (self.source / "func.py").unlink()
        (self.source / "func.py").symlink_to(self.source / ".env")
        with mock.patch.object(builder.subprocess, "run") as run:
            with self.assertRaisesRegex(builder.BuildError, "regular func.py"):
                builder.build_and_push(self.environment)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

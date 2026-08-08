"""
`optarena._cases._sandboxed_mcp_service`: the MockService-contract adapter
that proxies tool-use case execution through a real (fake-in-these-tests)
MCP session instead of an in-process mock. No real Docker/subprocess here -
`DockerSandbox`/`MCPStdioClient` are both faked, since the point of this
module's own tests is the adapter's wiring/contract, not the transport
(that's `test_mcp_client.py`/`test_sandbox_exec_attached.py`).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from optarena._cases import _sandboxed_mcp_service as svc_mod
from optarena._cases._sandboxed_mcp_service import (
    SandboxedMCPService,
    build_sandboxed_service,
    diff_case_asserted_arguments,
    diff_case_required_arguments,
    diff_case_tools_against_live,
    get_sandboxed_service_factory,
    resolve_tool_service_mode,
)
from optarena._cases._tool_evaluate import evaluate_tool_case


class FakeMCPClient:
    """Stands in for MCPStdioClient - same three methods SandboxedMCPService
    actually calls, no subprocess/pipes involved."""

    def __init__(self, tools=None, call_results=None):
        self._tools = tools if tools is not None else [
            {"name": "write_file", "description": "Write a file.",
             "inputSchema": {"type": "object", "properties": {"path": {"type": "string"},
                                                                "content": {"type": "string"}},
                              "required": ["path", "content"]}},
            {"name": "read_text_file", "description": "Read a file.",
             "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                              "required": ["path"]}},
        ]
        self._call_results = call_results or {}
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def initialize(self, client_info, timeout=None):
        return {"serverInfo": {"name": "fake"}}

    def list_tools(self, timeout=None):
        return self._tools

    def call_tool(self, name, arguments, timeout=None):
        self.calls.append((name, arguments))
        if name in self._call_results:
            return self._call_results[name]
        return {"ok": True}

    def close(self):
        self.closed = True


def _make_service(tmp_path: Path, **client_kwargs) -> tuple[SandboxedMCPService, mock.Mock, FakeMCPClient]:
    fake_sandbox = mock.Mock()
    fake_sandbox.exec_attached.return_value = mock.Mock()
    fake_client = FakeMCPClient(**client_kwargs)
    with mock.patch.object(svc_mod, "MCPStdioClient", return_value=fake_client):
        service = SandboxedMCPService(fake_sandbox, tmp_path, ["mcp-server-filesystem", "."], "filesystem")
    return service, fake_sandbox, fake_client


class RegistryTests(unittest.TestCase):
    #: Every service proven end-to-end against real Docker (see the
    #: implementation plan's tiered backlog) - kept in sync deliberately,
    #: not derived from SANDBOXED_SERVICES.keys() itself, so an entry added
    #: without a real build+smoke-test still shows up as a gap here.
    PROVEN_SERVICES = {
        "filesystem", "git_repo", "code_intel", "build_tools",
        "database", "observability", "package_registry", "cloud_infra",
        "docker", "kubernetes",
    }

    def test_known_service_returns_its_spec(self):
        spec = get_sandboxed_service_factory("filesystem")
        self.assertIn("image", spec)
        self.assertIn("command", spec)

    def test_unknown_service_raises_keyerror_with_available_list(self):
        with self.assertRaises(KeyError) as ctx:
            get_sandboxed_service_factory("not_a_real_service")
        self.assertIn("filesystem", str(ctx.exception))

    def test_registry_matches_the_services_actually_proven_against_real_docker(self):
        self.assertEqual(set(svc_mod.SANDBOXED_SERVICES), self.PROVEN_SERVICES)

    def test_every_registered_service_has_a_valid_spec_shape(self):
        for name, spec in svc_mod.SANDBOXED_SERVICES.items():
            with self.subTest(service=name):
                self.assertIsInstance(spec.get("image"), str)
                self.assertTrue(spec["image"], "image must be non-empty")
                self.assertIsInstance(spec.get("command"), list)
                self.assertTrue(spec["command"], "command must be non-empty")
                self.assertTrue(all(isinstance(c, str) for c in spec["command"]))
                if "extra_run_args" in spec:
                    self.assertIsInstance(spec["extra_run_args"], list)
                if "network" in spec:
                    # PF-2: "host" is deliberately NOT permitted - no service
                    # uses it, and allowing it here would let one appear
                    # without a conscious test edit.
                    self.assertIn(spec["network"], ("none", "bridge"))
                if "startup_timeout" in spec:
                    self.assertIsInstance(spec["startup_timeout"], (int, float))
                    self.assertGreater(spec["startup_timeout"], 30.0,
                                       "startup_timeout should only be set when it's actually "
                                       "longer than the client's own default")

    def test_every_registered_image_is_a_real_docker_images_entry(self):
        # Every sandboxed service's image must resolve through the same
        # DOCKER_IMAGES registry `optarena sandbox build/pull/status` and
        # `doctor` already introspect generically - not a bespoke string a
        # service entry invented on its own.
        from optarena._cases._constants import DOCKER_IMAGES
        for name, spec in svc_mod.SANDBOXED_SERVICES.items():
            with self.subTest(service=name):
                self.assertIn(spec["image"], DOCKER_IMAGES.values())

    def test_hardening_exceptions_are_exactly_the_reviewed_set(self):
        # A deliberate tripwire: if a future service quietly grows an
        # extra_run_args/network override, this forces a conscious update
        # here rather than a silent, unreviewed hardening change.
        # database: CAP_SETUID/SETGID (real Postgres refuses to run as root).
        # docker/kubernetes: docker-outside-of-docker host socket mount,
        # user-approved (see the implementation plan's Tier 3 note) rather
        # than full privileged docker-in-docker.
        # package_registry/kubernetes: "bridge" network - package_registry's
        # real server has no offline mode; kubernetes' sandbox must be able
        # to join kind's own docker network at runtime (Docker refuses
        # `network connect` on a --network none container outright).
        with_extra_caps = {n for n, s in svc_mod.SANDBOXED_SERVICES.items() if s.get("extra_run_args")}
        with_network_override = {n for n, s in svc_mod.SANDBOXED_SERVICES.items()
                                 if s.get("network", "none") != "none"}
        with_host_socket = {n for n, s in svc_mod.SANDBOXED_SERVICES.items()
                            if s.get("host_docker_socket")}
        self.assertEqual(with_extra_caps, {"database", "docker", "kubernetes"})
        self.assertEqual(with_network_override, {"package_registry", "kubernetes"})
        # S-3: exactly these two mount the host socket, and BOTH must carry
        # the flag that build_sandboxed_service's OPTARENA_ALLOW_HOST_DOCKER
        # gate keys off - an entry mounting docker.sock via extra_run_args
        # without the flag would silently skip the gate.
        self.assertEqual(with_host_socket, {"docker", "kubernetes"})
        for name, spec in svc_mod.SANDBOXED_SERVICES.items():
            mounts_socket = any("docker.sock" in a for a in spec.get("extra_run_args", []))
            self.assertEqual(mounts_socket, bool(spec.get("host_docker_socket")),
                             f"{name}: docker.sock mount and host_docker_socket flag must agree")


class DiffCaseToolsAgainstLiveTests(unittest.TestCase):
    def test_no_drift_when_every_referenced_tool_exists(self):
        case = {"tools": ["a", "b"], "expected_calls": [{"tool": "a"}], "forbidden_calls": [{"tool": "b"}]}
        self.assertEqual(diff_case_tools_against_live(case, {"a", "b", "c"}), [])

    def test_reports_missing_tools_from_every_referencing_field(self):
        case = {"tools": ["a", "missing1"], "expected_calls": [{"tool": "missing2"}],
                "forbidden_calls": [{"tool": "missing1"}]}
        self.assertEqual(diff_case_tools_against_live(case, {"a"}), ["missing1", "missing2"])

    def test_empty_case_has_no_drift(self):
        self.assertEqual(diff_case_tools_against_live({}, {"a"}), [])


class SandboxedMCPServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="optarena_test_sandboxed_svc_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_construction_discovers_live_tool_schemas(self):
        service, _, _ = _make_service(self.tmp)
        self.assertEqual(set(service.tool_schemas), {"write_file", "read_text_file"})
        self.assertEqual(service.tool_schemas["write_file"]["type"], "function")

    def test_tools_populated_for_evaluate_tool_case_unknown_call_accounting(self):
        # Regression coverage: evaluate_tool_case computes n_unknown_calls
        # from set(service.TOOLS) directly - if this were left empty (the
        # base MockService default), every real call would be misreported
        # as unknown.
        service, _, _ = _make_service(self.tmp)
        self.assertEqual(set(service.TOOLS), {"write_file", "read_text_file"})

    def test_dispatch_logs_call_and_returns_client_result(self):
        service, _, fake_client = _make_service(self.tmp, call_results={"write_file": {"bytes_written": 5}})
        result = service.dispatch("write_file", {"path": "a.txt", "content": "hello"})
        self.assertEqual(result, {"bytes_written": 5})
        self.assertEqual(service.call_log, [
            {"tool": "write_file", "arguments": {"path": "a.txt", "content": "hello"}, "result": {"bytes_written": 5}},
        ])
        self.assertEqual(fake_client.calls, [("write_file", {"path": "a.txt", "content": "hello"})])

    def test_dispatch_unknown_tool_short_circuits_without_calling_client(self):
        service, _, fake_client = _make_service(self.tmp)
        result = service.dispatch("delete_everything", {})
        self.assertEqual(result, {"error": "unknown tool 'delete_everything'"})
        self.assertEqual(fake_client.calls, [])  # never reached the live server

    def test_schemas_for_returns_requested_subset_in_order(self):
        service, _, _ = _make_service(self.tmp)
        schemas = service.schemas_for(["read_text_file", "write_file"])
        self.assertEqual([s["function"]["name"] for s in schemas], ["read_text_file", "write_file"])

    def test_schemas_for_all_when_none_requested(self):
        service, _, _ = _make_service(self.tmp)
        self.assertEqual(len(service.schemas_for(None)), 2)

    def test_schemas_for_unknown_name_raises_keyerror(self):
        service, _, _ = _make_service(self.tmp)
        with self.assertRaises(KeyError) as ctx:
            service.schemas_for(["no_such_tool"])
        self.assertIn("no_such_tool", str(ctx.exception))
        self.assertIn("filesystem", str(ctx.exception))

    def test_seed_writes_real_files_and_directories_onto_case_root(self):
        service, _, _ = _make_service(self.tmp)
        service.seed({"files": {"src/app.py": "print('hi')\n"}, "directories": ["empty_dir"]})
        self.assertEqual((self.tmp / "src" / "app.py").read_text(encoding="utf-8"), "print('hi')\n")
        self.assertTrue((self.tmp / "empty_dir").is_dir())

    def test_summary_reflects_real_files_written_via_seed_and_dispatch(self):
        service, _, _ = _make_service(self.tmp)
        service.seed({"files": {"a.txt": "one"}})
        (self.tmp / "b.txt").write_text("two", encoding="utf-8")  # simulates a real write_file call's effect
        summary = service.summary()
        self.assertEqual(summary["file_count"], 2)
        self.assertEqual(summary["files"], {"a.txt": "one", "b.txt": "two"})
        self.assertEqual(summary["n_calls"], 0)

    def test_close_closes_client_and_stops_the_dedicated_sandbox(self):
        service, fake_sandbox, fake_client = _make_service(self.tmp)
        service.close()
        self.assertTrue(fake_client.closed)
        fake_sandbox.stop.assert_called_once()

    def test_end_to_end_through_the_real_oracle(self):
        # Proves the adapter satisfies the same contract evaluate_tool_case
        # already relies on for mocks - construct a small case by hand
        # (not one of the shipped filesystem mock cases: those were authored
        # against the mock's bare-relative-path convention, which a real
        # server may not share for expected_calls' exact argument matching -
        # see SandboxedMCPService.seed's docstring. expected_final_state is
        # unaffected, since summary() reports in the mock's own convention.)
        service, _, _ = _make_service(self.tmp, call_results={"write_file": {"path": "notes.txt", "bytes_written": 5}})
        service.seed({})
        service.dispatch("write_file", {"path": "notes.txt", "content": "hello"})
        # The FakeMCPClient only records the call - it doesn't touch disk the
        # way a real sandboxed write_file call would. Simulate that real
        # side effect directly (summary() reads case_root off the host disk,
        # same as test_summary_reflects_real_files_written_via_seed_and_dispatch above).
        (self.tmp / "notes.txt").write_text("hello", encoding="utf-8")
        case = {
            "expected_calls": [{"tool": "write_file", "arguments_contains": {"path": "notes.txt"}}],
            "forbidden_calls": [{"tool": "read_text_file"}],
            "expected_final_state": {"file_count": 1},
        }
        failures, info = evaluate_tool_case(case, service)
        self.assertEqual(failures, [])
        self.assertEqual(info["n_unknown_calls"], 0)


class BuildSandboxedServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="optarena_test_build_sandboxed_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.case_root = self.tmp / "case_a"
        self.case_root.mkdir()

    def test_unknown_service_raises_before_touching_any_sandbox(self):
        with mock.patch.object(svc_mod, "DockerSandbox") as sandbox_cls:
            with self.assertRaises(KeyError):
                build_sandboxed_service("not_a_real_service", self.case_root)
        sandbox_cls.assert_not_called()

    def test_sandbox_start_failure_raises_runtimeerror(self):
        fake_sandbox = mock.Mock()
        fake_sandbox.start.return_value = False
        with mock.patch.object(svc_mod, "DockerSandbox", return_value=fake_sandbox), \
             mock.patch.object(svc_mod, "resolve_image", side_effect=lambda x: x):
            with self.assertRaises(RuntimeError) as ctx:
                build_sandboxed_service("filesystem", self.case_root)
        self.assertIn("filesystem", str(ctx.exception))

    def test_happy_path_mounts_the_case_dir_itself_never_its_parent(self):
        fake_sandbox = mock.Mock()
        fake_sandbox.start.return_value = True
        fake_sandbox.exec_attached.return_value = mock.Mock()
        fake_sandbox.name = "optarena-sandbox-t1"
        fake_client = FakeMCPClient()
        with mock.patch.object(svc_mod, "DockerSandbox", return_value=fake_sandbox) as sandbox_cls, \
             mock.patch.object(svc_mod, "resolve_image", side_effect=lambda x: x), \
             mock.patch.object(svc_mod, "MCPStdioClient", return_value=fake_client):
            service = build_sandboxed_service("filesystem", self.case_root)

        self.assertIsInstance(service, SandboxedMCPService)
        # S-4 (tool-call audit): the bind-mount root is the CASE directory,
        # never its parent - the parent is the shared run root (other
        # cases' workspaces, hidden test files). This assertion is the
        # pinned-mount test the original bug would have been caught by.
        # extra_run_args=None: filesystem's registry entry has no
        # extra_run_args key (unlike e.g. database's SETUID/SETGID grant).
        # network="none": the default - filesystem has no "network" key
        # (unlike package_registry's deliberate "bridge" exception).
        # register=False (E-1): a per-case dedicated sandbox never joins
        # the shared check_command routing registry.
        sandbox_cls.assert_called_once_with(self.case_root, image="optarena-tester-mcp-filesystem:latest",
                                            extra_run_args=None, network="none", register=False)
        fake_sandbox.start.assert_called_once()
        # The sandbox's own name rides into the container env, so wrapper
        # scripts can derive host-reapable resource names (B-2).
        _, exec_kwargs = fake_sandbox.exec_attached.call_args
        self.assertEqual(exec_kwargs["env"], {"OPTARENA_SANDBOX_NAME": "optarena-sandbox-t1"})

    def test_registry_entrys_extra_run_args_are_forwarded_to_dockersandbox(self):
        # database's registry entry grants SETUID/SETGID (real Postgres
        # refuses to run as root - its startup script needs `su postgres`) -
        # confirm build_sandboxed_service actually forwards this, not just
        # that the registry dict happens to declare it.
        fake_sandbox = mock.Mock()
        fake_sandbox.start.return_value = True
        fake_sandbox.exec_attached.return_value = mock.Mock()
        fake_client = FakeMCPClient()
        with mock.patch.object(svc_mod, "DockerSandbox", return_value=fake_sandbox) as sandbox_cls, \
             mock.patch.object(svc_mod, "resolve_image", side_effect=lambda x: x), \
             mock.patch.object(svc_mod, "MCPStdioClient", return_value=fake_client):
            build_sandboxed_service("database", self.case_root)
        _, kwargs = sandbox_cls.call_args
        self.assertEqual(kwargs["extra_run_args"], ["--cap-add", "SETUID", "--cap-add", "SETGID"])

    def test_package_registrys_network_override_is_forwarded_to_dockersandbox(self):
        # package_registry's real server has no offline-registry config
        # option (confirmed empirically) but also no publish/mutate tool -
        # its registry entry deliberately overrides network to "bridge".
        # Confirm build_sandboxed_service actually forwards this too.
        fake_sandbox = mock.Mock()
        fake_sandbox.start.return_value = True
        fake_sandbox.exec_attached.return_value = mock.Mock()
        fake_client = FakeMCPClient()
        with mock.patch.object(svc_mod, "DockerSandbox", return_value=fake_sandbox) as sandbox_cls, \
             mock.patch.object(svc_mod, "resolve_image", side_effect=lambda x: x), \
             mock.patch.object(svc_mod, "MCPStdioClient", return_value=fake_client):
            build_sandboxed_service("package_registry", self.case_root)
        _, kwargs = sandbox_cls.call_args
        self.assertEqual(kwargs["network"], "bridge")

    def test_handshake_failure_stops_the_sandbox_before_reraising(self):
        fake_sandbox = mock.Mock()
        fake_sandbox.start.return_value = True
        fake_sandbox.exec_attached.return_value = mock.Mock()
        with mock.patch.object(svc_mod, "DockerSandbox", return_value=fake_sandbox), \
             mock.patch.object(svc_mod, "resolve_image", side_effect=lambda x: x), \
             mock.patch.object(svc_mod, "MCPStdioClient", side_effect=RuntimeError("handshake boom")):
            with self.assertRaises(RuntimeError):
                build_sandboxed_service("filesystem", self.case_root)
        fake_sandbox.stop.assert_called_once()


class ResolveToolServiceModeTests(unittest.TestCase):
    """S-2: the single shared precedence rule - sandboxed is USER-granted,
    a case may opt down but never up. Used identically by tool_chat and the
    run manifest, so this table IS the contract."""

    def test_full_precedence_table(self):
        for case_mode, user_mode, expected in [
            (None, None, "mock"),
            (None, "mock", "mock"),
            (None, "sandboxed", "sandboxed"),
            ("mock", None, "mock"),
            ("mock", "mock", "mock"),
            ("mock", "sandboxed", "mock"),          # case opts DOWN - honored
            ("sandboxed", None, "mock"),            # case can't opt UP
            ("sandboxed", "mock", "mock"),          # case can't override the user
            ("sandboxed", "sandboxed", "sandboxed"),
        ]:
            with self.subTest(case=case_mode, user=user_mode):
                self.assertEqual(resolve_tool_service_mode(case_mode, user_mode), expected)


class HostDockerSocketGateTests(unittest.TestCase):
    """S-3: services that mount the HOST Docker socket hand the model under
    test reach into the real daemon - explicit per-environment opt-in,
    never ambient."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="optarena_test_gate_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_docker_and_kubernetes_refused_without_the_env_opt_in(self):
        import os
        for name in ("docker", "kubernetes"):
            with self.subTest(service=name):
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("OPTARENA_ALLOW_HOST_DOCKER", None)
                    with mock.patch.object(svc_mod, "DockerSandbox") as sandbox_cls:
                        with self.assertRaises(RuntimeError) as ctx:
                            build_sandboxed_service(name, self.tmp)
                    sandbox_cls.assert_not_called()   # refused before any container work
                self.assertIn("OPTARENA_ALLOW_HOST_DOCKER", str(ctx.exception))
                self.assertIn("HOST Docker socket", str(ctx.exception))

    def test_env_opt_in_allows_the_build(self):
        import os
        fake_sandbox = mock.Mock()
        fake_sandbox.start.return_value = True
        fake_sandbox.exec_attached.return_value = mock.Mock()
        fake_sandbox.name = "optarena-sandbox-g1"
        with mock.patch.dict(os.environ, {"OPTARENA_ALLOW_HOST_DOCKER": "1"}), \
             mock.patch.object(svc_mod, "DockerSandbox", return_value=fake_sandbox), \
             mock.patch.object(svc_mod, "resolve_image", side_effect=lambda x: x), \
             mock.patch.object(svc_mod, "MCPStdioClient", return_value=FakeMCPClient()):
            service = build_sandboxed_service("docker", self.tmp)
        self.assertIsInstance(service, SandboxedMCPService)

    def test_non_socket_services_need_no_opt_in(self):
        import os
        fake_sandbox = mock.Mock()
        fake_sandbox.start.return_value = True
        fake_sandbox.exec_attached.return_value = mock.Mock()
        fake_sandbox.name = "optarena-sandbox-g2"
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPTARENA_ALLOW_HOST_DOCKER", None)
            with mock.patch.object(svc_mod, "DockerSandbox", return_value=fake_sandbox), \
                 mock.patch.object(svc_mod, "resolve_image", side_effect=lambda x: x), \
                 mock.patch.object(svc_mod, "MCPStdioClient", return_value=FakeMCPClient()):
                service = build_sandboxed_service("filesystem", self.tmp)
        self.assertIsInstance(service, SandboxedMCPService)


class SeedContainmentTests(unittest.TestCase):
    """S-1: tool_service_seed paths are untrusted case content that become
    REAL host disk writes in this mode - runtime containment on top of the
    schema-level gate (tested in test_optarena.py's SchemaValidationTests)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="optarena_test_seed_contain_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.service, _, _ = _make_service(self.tmp)

    def test_traversal_file_path_raises_before_any_write(self):
        with self.assertRaises(ValueError) as ctx:
            self.service.seed({"files": {"../evil.txt": "x"}})
        self.assertIn("escapes the case workspace", str(ctx.exception))
        self.assertFalse((self.tmp.parent / "evil.txt").exists())

    def test_absolute_file_path_raises(self):
        # Path.__truediv__ with an absolute RHS REPLACES the base entirely -
        # the exact pathlib behavior that makes join-then-verify mandatory.
        import sys
        abs_path = "C:/absolutely/elsewhere.txt" if sys.platform == "win32" else "/absolutely/elsewhere.txt"
        with self.assertRaises(ValueError):
            self.service.seed({"files": {abs_path: "x"}})

    def test_traversal_directory_raises(self):
        with self.assertRaises(ValueError):
            self.service.seed({"directories": ["../../outside"]})

    def test_legitimate_nested_paths_still_work(self):
        self.service.seed({"files": {"src/deep/app.py": "print('hi')\n"},
                           "directories": ["empty/nested"]})
        self.assertEqual((self.tmp / "src" / "deep" / "app.py").read_text(encoding="utf-8"),
                         "print('hi')\n")
        self.assertTrue((self.tmp / "empty" / "nested").is_dir())


class RequiredArgumentDriftTests(unittest.TestCase):
    """B-3: name-level drift isn't the drift that bites - required-argument
    drift (git_repo's repo_path, confirmed live) is."""

    def _live(self, required):
        return {"git_status": {"type": "function", "function": {
            "name": "git_status", "description": "d",
            "parameters": {"type": "object", "properties": {}, "required": required}}}}

    def test_reports_arguments_the_real_server_newly_requires(self):
        case = {"expected_calls": [{"tool": "git_status"}]}
        out = diff_case_required_arguments(case, "git_repo", self._live(["repo_path"]))
        self.assertEqual(len(out), 1)
        self.assertIn("git_status", out[0])
        self.assertIn("repo_path", out[0])

    def test_silent_when_requirements_match_the_mock(self):
        case = {"expected_calls": [{"tool": "git_status"}]}
        # The mock's git_status requires nothing; a live server requiring
        # nothing either is drift-free.
        self.assertEqual(diff_case_required_arguments(case, "git_repo", self._live([])), [])

    def test_tools_missing_on_either_side_are_not_this_checks_job(self):
        case = {"expected_calls": [{"tool": "not_a_real_tool"}]}
        self.assertEqual(diff_case_required_arguments(case, "git_repo", self._live(["repo_path"])), [])

    def test_unknown_service_returns_empty(self):
        self.assertEqual(diff_case_required_arguments({}, "no_such_service", {}), [])


class AssertedArgumentDriftTests(unittest.TestCase):
    """The blocking check: an `arguments_contains` key the real tool doesn't
    accept can never match, so the case is guaranteed to fail sandboxed.
    Measured against the real corpus this caught exactly one case
    (`git_add.paths` vs the real server's `files`) where the advisory
    required-args check produced nine non-predictive warnings."""

    def _live(self, accepted):
        return {"git_add": {"type": "function", "function": {
            "name": "git_add", "description": "d",
            "parameters": {"type": "object",
                           "properties": {k: {"type": "string"} for k in accepted},
                           "required": []}}}}

    def test_asserted_key_absent_from_the_real_tool_is_reported(self):
        case = {"expected_calls": [{"tool": "git_add", "arguments_contains": {"paths": ["a"]}}]}
        out = diff_case_asserted_arguments(case, self._live(["files", "repo_path"]))
        self.assertEqual(len(out), 1)
        self.assertIn("git_add.paths", out[0])
        self.assertIn("files", out[0])

    def test_asserted_key_the_real_tool_accepts_is_clean(self):
        case = {"expected_calls": [{"tool": "git_add", "arguments_contains": {"files": ["a"]}}]}
        self.assertEqual(diff_case_asserted_arguments(case, self._live(["files", "repo_path"])), [])

    def test_newly_required_arguments_are_not_this_checks_business(self):
        # The whole point: repo_path being newly REQUIRED breaks nothing,
        # because arguments_contains is a subset match.
        case = {"expected_calls": [{"tool": "git_add", "arguments_contains": {"files": ["a"]}}]}
        self.assertEqual(diff_case_asserted_arguments(case, self._live(["files", "repo_path"])), [])

    def test_forbidden_calls_are_checked_too(self):
        case = {"forbidden_calls": [{"tool": "git_add", "arguments_contains": {"paths": ["a"]}}]}
        self.assertEqual(len(diff_case_asserted_arguments(case, self._live(["files"]))), 1)

    def test_missing_tool_is_left_to_the_name_check(self):
        case = {"expected_calls": [{"tool": "git_blame", "arguments_contains": {"x": 1}}]}
        self.assertEqual(diff_case_asserted_arguments(case, self._live(["files"])), [])

    def test_assertion_free_specs_are_clean(self):
        case = {"expected_calls": [{"tool": "git_add"}]}
        self.assertEqual(diff_case_asserted_arguments(case, self._live(["files"])), [])


class ShippedCorpusSandboxReadinessTests(unittest.TestCase):
    """Pins what the REAL shipped corpus looks like against the REAL
    servers' schemas (recorded from live runs, so this is a regression
    guard on the corpus, not a restatement of the code). No Docker needed -
    the live schemas are the recorded fact being asserted against."""

    #: Recorded live from `mcp-server-git` 2026.7.10 via the drift check.
    GIT_REAL_TOOLS = {
        "git_status", "git_diff_unstaged", "git_diff_staged", "git_diff", "git_commit",
        "git_add", "git_reset", "git_log", "git_create_branch", "git_checkout",
        "git_show", "git_branch",
    }

    def test_git_repo_cases_split_exactly_as_measured_live(self):
        from optarena.cases import filter_cases, load_cases
        cases = filter_cases(load_cases(), tool_service="git_repo")
        blocked = [c["name"] for c in cases
                   if diff_case_tools_against_live(c, self.GIT_REAL_TOOLS)]
        # The 4 blocked cases are blocked by REAL capability gaps - the
        # official server registers no blame/push/pull/remote/tag tools at
        # all - not by anything fixable in the corpus.
        self.assertEqual(sorted(blocked), [
            "tool_git_blame_find_line_origin",
            "tool_git_pull_before_push",
            "tool_git_push_after_commit",
            "tool_git_tag_release",
        ])
        self.assertEqual(len(cases) - len(blocked), 8, "8 of 12 git_repo cases are sandboxed-ready")

    def test_no_git_case_still_asserts_the_pre_fidelity_fix_argument_name(self):
        # Regression guard for the mock/real divergence this cycle fixed:
        # the real server's argument is `files`, the mock said `paths`.
        from optarena.cases import filter_cases, load_cases
        for c in filter_cases(load_cases(), tool_service="git_repo"):
            for field in ("expected_calls", "forbidden_calls"):
                for spec in c.get(field) or []:
                    self.assertNotIn("paths", spec.get("arguments_contains") or {},
                                     f"{c['name']}: uses the old mock-only argument name")


class CloseOrderingTests(unittest.TestCase):
    """B-2: close() = stdin EOF -> wait shutdown_grace for the server's own
    graceful exit (in-container cleanup traps run here) -> stop container ->
    host-side extra_cleanup reaper for resources that outlive the container
    BY DESIGN (kind's node containers are host siblings)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="optarena_test_close_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _service(self, **kwargs):
        fake_sandbox = mock.Mock()
        fake_proc = mock.Mock()
        fake_sandbox.exec_attached.return_value = fake_proc
        with mock.patch.object(svc_mod, "MCPStdioClient", return_value=FakeMCPClient()):
            service = SandboxedMCPService(fake_sandbox, self.tmp, ["srv"], "filesystem", **kwargs)
        return service, fake_sandbox, fake_proc

    def test_close_waits_for_graceful_exit_then_stops_then_reaps(self):
        order = []
        service, fake_sandbox, fake_proc = self._service(
            shutdown_grace=7.5, extra_cleanup=lambda: order.append("reap"))
        fake_proc.wait.side_effect = lambda timeout: order.append(("wait", timeout))
        fake_sandbox.stop.side_effect = lambda: order.append("stop")
        service.close()
        self.assertEqual(order, [("wait", 7.5), "stop", "reap"])

    def test_reaper_failure_never_raises_out_of_close(self):
        def boom():
            raise OSError("docker went away")
        service, _, _ = self._service(extra_cleanup=boom)
        service.close()   # must not raise

    def test_kind_cluster_spec_derives_a_host_reapable_node_name(self):
        import os
        fake_sandbox = mock.Mock()
        fake_sandbox.start.return_value = True
        fake_sandbox.exec_attached.return_value = mock.Mock()
        fake_sandbox.name = "optarena-sandbox-k1"
        with mock.patch.dict(os.environ, {"OPTARENA_ALLOW_HOST_DOCKER": "1"}), \
             mock.patch.object(svc_mod, "DockerSandbox", return_value=fake_sandbox), \
             mock.patch.object(svc_mod, "resolve_image", side_effect=lambda x: x), \
             mock.patch.object(svc_mod, "MCPStdioClient", return_value=FakeMCPClient()), \
             mock.patch.object(svc_mod.subprocess, "run") as run, \
             mock.patch.object(svc_mod, "container_engine", return_value="docker"):
            service = build_sandboxed_service("kubernetes", self.tmp)
            service.close()
        reap_calls = [c for c in run.call_args_list
                      if c[0][0][:3] == ["docker", "rm", "-f"]]
        self.assertEqual(len(reap_calls), 1)
        self.assertEqual(reap_calls[0][0][0][3], "optarena-optarena-sandbox-k1-control-plane")


if __name__ == "__main__":
    unittest.main()

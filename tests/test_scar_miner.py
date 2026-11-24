"""Behavioral tests for Rapid-Regret Miner library and thin CLI wrapper (GH-117)."""

import ast
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.agent_paths import AgentPathRegistry
from lib.scar_miner import (
    CATEGORY_SPOKE_FILES,
    TIER_ID_PREFIXES,
    UPSTREAM_REPO,
    MarkdownScarStore,
    MinedScarRecord,
    ScarConfigStore,
    ScarMinerScript,
    UpstreamIssuePublisher,
    category_to_filename,
    validate_mined_scar,
)
from lib.scar_ontology import DOMAIN_CATEGORY_ONTOLOGY


def _sample_record(
    title="Guard Empty Database URLs",
    category="DB",
    circuit_breaker="If URL is empty, halt before opening a connection.",
):
  """Construct a valid Simple English MinedScarRecord fixture."""
  return MinedScarRecord(
      title=title,
      category=category,
      wound="An empty database URL opened an unintended local file.",
      trap="Callers assumed configuration loading always returned a valid connection string.",
      circuit_breaker=circuit_breaker,
      reflex="Validate the connection URL before opening a database handle.",
      socratic_probe="Did you validate the database URL before opening a connection?",
  )


class TestScarMinerLayerSeparation(unittest.TestCase):
  """Tests SCAR-ARCH-05 and SCAR-GENUI-0008 layer separation invariants."""

  def test_lib_scar_miner_has_no_shebang(self):
    """Verifies lib/scar_miner.py has no executable shebang header."""
    lib_path = os.path.join(REPO_ROOT, "lib", "scar_miner.py")
    with open(lib_path, "r", encoding="utf-8") as fh:
      first_line = fh.readline()
    self.assertFalse(first_line.startswith("#!"))

  def test_scripts_scar_miner_is_thin_executable_wrapper(self):
    """Verifies scripts/scar_miner.py is executable with zero local defs/classes."""
    script_path = os.path.join(REPO_ROOT, "scripts", "scar_miner.py")
    self.assertTrue(os.path.isfile(script_path))
    mode = os.stat(script_path).st_mode
    self.assertTrue(bool(mode & stat.S_IXUSR), "scripts/scar_miner.py must be executable")

    with open(script_path, "r", encoding="utf-8") as fh:
      source = fh.read()
    self.assertTrue(source.startswith("#!/usr/bin/env python3"))
    self.assertIn("sys.exit(ScarMinerScript().execute())", source)

    tree = ast.parse(source, filename=script_path)
    local_defs = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    self.assertEqual(local_defs, [])

    spec = importlib.util.spec_from_file_location("scar_miner_wrapper", script_path)
    self.assertIsNotNone(spec)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    self.assertIn("ScarMinerScript", module.__all__)
    self.assertIs(module.ScarMinerScript, ScarMinerScript)


class TestCategoryMappingAndValidation(unittest.TestCase):
  """Tests 68-code ontology filename mapping, traversal guards, and scar validation."""

  def test_all_68_ontology_codes_map_to_safe_markdown_filenames(self):
    """Verifies every canonical domain code maps to a safe categorized .md file."""
    self.assertEqual(set(CATEGORY_SPOKE_FILES.keys()), set(DOMAIN_CATEGORY_ONTOLOGY.keys()))
    for code in DOMAIN_CATEGORY_ONTOLOGY:
      with self.subTest(code=code):
        fname = category_to_filename(code)
        self.assertTrue(fname.endswith(".md"))
        self.assertNotIn("/", fname)
        self.assertNotIn("\\", fname)
        self.assertNotIn("..", fname)

    self.assertEqual(category_to_filename("DATA"), "data.md")
    self.assertEqual(category_to_filename("Database"), "data.md")
    self.assertEqual(category_to_filename("PROC"), "process.md")
    self.assertEqual(category_to_filename("Testing"), "testing.md")
    self.assertEqual(category_to_filename("ARCH"), "architecture.md")
    self.assertEqual(category_to_filename("SEC"), "security.md")
    self.assertEqual(category_to_filename("GIT"), "git_and_shell.md")
    self.assertEqual(category_to_filename("SYS"), "system.md")
    self.assertEqual(category_to_filename("TOOL"), "tooling.md")

  def test_category_rejects_path_traversal_inputs(self):
    """Verifies path traversal payloads in category raise ValueError."""
    for bad in ("../evil", "../../etc/passwd", "foo/bar", "a\\b", "proc\x00.md"):
      with self.subTest(bad=bad):
        with self.assertRaises(ValueError):
          category_to_filename(bad)

  def test_validate_mined_scar_accepts_valid_and_rejects_invalid(self):
    """Verifies validation enforces four-part anatomy, Simple English, and Socratic probe rules."""
    valid = _sample_record()
    self.assertEqual(validate_mined_scar(valid), [])

    valid_no_cb = _sample_record(circuit_breaker="")
    self.assertEqual(validate_mined_scar(valid_no_cb), [])

    bad_probe = MinedScarRecord(
        title="Bad Probe",
        category="TEST",
        wound="A test leaked state.",
        trap="The test skipped cleanup.",
        reflex="Clean up temp files after each test.",
        socratic_probe="This probe has no question mark.",
    )
    probe_errors = validate_mined_scar(bad_probe)
    self.assertTrue(any("?" in err for err in probe_errors))

    bad_contraction = MinedScarRecord(
        title="Contraction Violation",
        category="PROC",
        wound="It's broken because tests don't isolate state.",
        trap="Agents skip temp directories.",
        reflex="Use isolated temporary directories.",
        socratic_probe="Did you isolate temporary directories?",
    )
    contraction_errors = validate_mined_scar(bad_contraction)
    self.assertTrue(any("contraction" in err.lower() for err in contraction_errors))

    missing_title = MinedScarRecord(
        title="   ",
        category="ARCH",
        wound="A module leaked internals.",
        trap="Callers imported private helpers.",
        reflex="Export a narrow public API.",
        socratic_probe="Does the module expose only public symbols?",
    )
    title_errors = validate_mined_scar(missing_title)
    self.assertTrue(any("title" in err.lower() for err in title_errors))

  def test_mined_scar_record_omits_unused_workflow_phases_field(self):
    """Verifies unused workflow_phases attribute is removed from MinedScarRecord (NBO #1)."""
    self.assertNotIn("workflow_phases", MinedScarRecord.__dataclass_fields__)

  def test_validate_mined_scar_rejects_multiline_applies_when(self):
    """Verifies applies_when rejects newline and carriage-return characters (BLOCKER-3)."""
    for bad_applies in (
        "Target 30\n### SCAR-LOCAL-DB-9999: Injected",
        "Target 30\rSingle line broken",
    ):
      with self.subTest(bad_applies=bad_applies):
        rec = MinedScarRecord(
            title="Guard Applies When",
            category="DB",
            wound="An empty database URL opened an unintended local file.",
            trap="Callers assumed configuration loading always returned a valid connection string.",
            reflex="Validate the connection URL before opening a database handle.",
            socratic_probe="Did you validate the database URL before opening a connection?",
            applies_when=bad_applies,
        )
        errors = validate_mined_scar(rec)
        self.assertTrue(
            any("applies_when" in err for err in errors),
            msg=f"Expected applies_when error, got: {errors}",
        )


class TestMarkdownScarStoreAndConfigStore(unittest.TestCase):
  """Tests categorized Markdown persistence, collision-free IDs, parsing, and config saving."""

  def test_save_and_parse_round_trip_with_tier_scoped_ids(self):
    """Verifies categorized file writes, optional Circuit Breaker, sequential IDs, and zero binary DBs."""
    with tempfile.TemporaryDirectory() as tmpdir:
      store = MarkdownScarStore()
      self.assertEqual(TIER_ID_PREFIXES["project"], "LOCAL")
      self.assertEqual(TIER_ID_PREFIXES["user"], "USER")
      self.assertEqual(TIER_ID_PREFIXES["custom"], "CUSTOM")

      first = store.save_scar(tmpdir, _sample_record(title="First DB Guard"), tier="project")
      second = store.save_scar(
          tmpdir,
          _sample_record(title="Second DB Guard", circuit_breaker=""),
          tier="project",
      )
      self.assertEqual(first["scarId"], "SCAR-LOCAL-DB-0001")
      self.assertEqual(second["scarId"], "SCAR-LOCAL-DB-0002")
      self.assertEqual(os.path.basename(first["filePath"]), "data.md")
      self.assertEqual(first["filePath"], second["filePath"])

      # Verify zero SQLite files created in markdown scar directory
      created_files = os.listdir(tmpdir)
      self.assertEqual(created_files, ["data.md"])

      # Parse back and verify both records preserve Circuit Breaker presence/absence
      parsed = store.parse_directory(tmpdir, source_tier="local")
      self.assertEqual(len(parsed), 2)
      self.assertEqual(parsed[0]["id"], "SCAR-LOCAL-DB-0001")
      self.assertEqual(parsed[0]["title"], "First DB Guard")
      self.assertEqual(
          parsed[0]["circuit_breaker"],
          "If URL is empty, halt before opening a connection.",
      )
      self.assertEqual(parsed[1]["id"], "SCAR-LOCAL-DB-0002")
      self.assertEqual(parsed[1]["circuit_breaker"], "")
      self.assertEqual(parsed[1]["source"], "local")

      # User and custom tier IDs use their own prefixes
      with tempfile.TemporaryDirectory() as user_dir, tempfile.TemporaryDirectory() as custom_dir:
        user_receipt = store.save_scar(user_dir, _sample_record(category="PROC"), tier="user")
        custom_receipt = store.save_scar(custom_dir, _sample_record(category="TEST"), tier="custom")
        self.assertEqual(user_receipt["scarId"], "SCAR-USER-PROC-0001")
        self.assertEqual(os.path.basename(user_receipt["filePath"]), "process.md")
        self.assertEqual(custom_receipt["scarId"], "SCAR-CUSTOM-TEST-0001")
        self.assertEqual(os.path.basename(custom_receipt["filePath"]), "testing.md")

  def test_scar_config_store_updates_scopes_without_clobbering_keys(self):
    """Verifies user-global, project-local, and both config persistence preserve unrelated keys."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      registry = AgentPathRegistry(home_dir=fake_home)
      cfg_store = ScarConfigStore(registry=registry)

      local_cfg_path = os.path.join(ws_dir, ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"models": {"tdd-worker": "pro"}, "ci": {"quiescenceSeconds": 0}}, fh)

      global_cfg_path = registry.get_profile("cursor").global_config_path
      os.makedirs(os.path.dirname(global_cfg_path), exist_ok=True)
      with open(global_cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"state": {"completedCardAction": "archive"}}, fh)

      custom_dir_a = os.path.join(fake_home, "team_scars_a")
      custom_dir_b = os.path.join(fake_home, "team_scars_b")

      res_both = cfg_store.add_scar_directory(
          custom_dir_a,
          scope="both",
          workspace_dir=ws_dir,
          agent="cursor",
      )
      self.assertEqual(set(res_both["updatedFiles"]), {global_cfg_path, local_cfg_path})

      # Re-adding the same directory is idempotent (no duplicate entry)
      cfg_store.add_scar_directory(
          custom_dir_a,
          scope="project-local",
          workspace_dir=ws_dir,
          agent="cursor",
      )
      cfg_store.add_scar_directory(
          custom_dir_b,
          scope="user-global",
          workspace_dir=ws_dir,
          agent="cursor",
      )

      with open(local_cfg_path, "r", encoding="utf-8") as fh:
        local_data = json.load(fh)
      self.assertEqual(local_data["models"]["tdd-worker"], "pro")
      self.assertEqual(local_data["ci"]["quiescenceSeconds"], 0)
      self.assertEqual(local_data["scars"]["directories"], [custom_dir_a])

      with open(global_cfg_path, "r", encoding="utf-8") as fh:
        global_data = json.load(fh)
      self.assertEqual(global_data["state"]["completedCardAction"], "archive")
      self.assertEqual(global_data["scars"]["directories"], [custom_dir_a, custom_dir_b])

      with self.assertRaises(ValueError):
        cfg_store.add_scar_directory(custom_dir_a, scope="invalid-scope", workspace_dir=ws_dir)

  def test_multiline_paragraphs_and_fenced_code_blocks_round_trip_losslessly(self):
    """Verifies multi-line prose and fenced code blocks with --- and ## round-trip losslessly (BLOCKER-1)."""
    wound_text = (
        "Parser dropped scars containing YAML frontmatter examples.\n\n"
        "```yaml\n"
        "---\n"
        "## Not a Section Heading\n"
        "### SCAR-LOCAL-DB-9999: Fake Heading Inside Fence\n"
        "- **Trap**: Fake bullet inside fence.\n"
        "nested:\n"
        "  child:\n"
        "    value: 42\n"
        "```\n\n"
        "Second paragraph explains the failure impact."
    )
    reflex_text = (
        "Track fenced code block state before matching headings.\n\n"
        "```python\n"
        "def keep_indent(lines):\n"
        "    result = []\n"
        "\n"
        "    for line in lines:\n"
        "        result.append(line)\n"
        "    return result\n"
        "```"
    )
    rec1 = MinedScarRecord(
        title="Preserve Fenced Code Blocks",
        category="DB",
        wound=wound_text,
        trap="The parser stripped indentation and flushed on horizontal rules inside fences.",
        circuit_breaker="If inside a fenced code block, ignore heading markers.",
        reflex=reflex_text,
        socratic_probe="Does your parser track fenced code blocks before splitting sections?",
    )
    rec2 = _sample_record(title="Follow Up Record", category="DB")

    with tempfile.TemporaryDirectory() as tmpdir:
      store = MarkdownScarStore()
      r1 = store.save_scar(tmpdir, rec1, tier="project")
      r2 = store.save_scar(tmpdir, rec2, tier="project")
      self.assertEqual(r1["scarId"], "SCAR-LOCAL-DB-0001")
      self.assertEqual(r2["scarId"], "SCAR-LOCAL-DB-0002")

      parsed = store.parse_directory(tmpdir, source_tier="local")
      self.assertEqual(len(parsed), 2)
      self.assertEqual(parsed[0]["id"], "SCAR-LOCAL-DB-0001")
      self.assertEqual(parsed[0]["wound"], wound_text)
      self.assertEqual(parsed[0]["reflex"], reflex_text)
      self.assertEqual(parsed[1]["id"], "SCAR-LOCAL-DB-0002")
      self.assertEqual(parsed[1]["title"], "Follow Up Record")

      # Tilde fences (~~~) also shield --- and ## lines and preserve exact indentation
      tilde_path = os.path.join(tmpdir, "tilde_case.md")
      tilde_wound = (
          "Tilde fenced example.\n\n"
          "~~~yaml\n"
          "---\n"
          "## Still Inside Tilde Fence\n"
          "root:\n"
          "  indent: true\n"
          "~~~"
      )
      with open(tilde_path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Synthetic Scars: Tilde\n\n"
            "### SCAR-LOCAL-ARCH-0001: Tilde Fence Guard\n\n"
            "- **Category**: `ARCH`\n"
            f"- **Wound**: {tilde_wound}\n"
            "- **Trap**: Ignored tilde fences.\n"
            "- **Permanent Reflex**: Track both backtick and tilde fences.\n"
            "- **Socratic Probe**: Does the parser handle tilde fences?\n"
        )
      tilde_parsed = store.parse_markdown_file(tilde_path, source_tier="local")
      self.assertEqual(len(tilde_parsed), 1)
      self.assertEqual(tilde_parsed[0]["wound"], tilde_wound)

  def test_parse_directory_skips_malformed_and_incomplete_markdown_safely(self):
    """Verifies malformed, incomplete, and non-UTF8 files are skipped without dropping valid scars."""
    with tempfile.TemporaryDirectory() as tmpdir:
      mixed_path = os.path.join(tmpdir, "architecture.md")
      with open(mixed_path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Random Preamble\n\nSome notes outside any scar.\n\n"
            "### SCAR-LOCAL-ARCH-0001: Incomplete Missing Trap\n\n"
            "- **Wound**: Only a wound is present.\n\n"
            "### SCAR-LOCAL-ARCH-0002: Valid Complete Scar\n\n"
            "- **Category**: `ARCH`\n"
            "- **Wound**: Missing validation broke startup.\n"
            "- **Trap**: Assumed file format was always valid.\n"
            "- **Permanent Reflex**: Skip incomplete records safely.\n"
            "- **Socratic Probe**: Does parsing skip incomplete records safely?\n\n"
            "### SCAR-LOCAL-ARCH-0003: Missing Probe\n\n"
            "- **Wound**: Wound present.\n"
            "- **Trap**: Trap present.\n"
            "- **Permanent Reflex**: Reflex present.\n"
        )
      binary_path = os.path.join(tmpdir, "zz_corrupt.md")
      with open(binary_path, "wb") as fh:
        fh.write(b"\xff\xfe\x00\x80invalid-utf8")

      parsed = MarkdownScarStore().parse_directory(tmpdir, source_tier="local")
      self.assertEqual([row["id"] for row in parsed], ["SCAR-LOCAL-ARCH-0002"])

  def test_save_scar_rejects_invalid_target_dir_without_filesystem_mutation(self):
    """Verifies save_scar rejects empty, whitespace, null-byte, non-string, and file target_dir (BLOCKER-3)."""
    store = MarkdownScarStore()
    rec = _sample_record()
    with tempfile.TemporaryDirectory() as sandbox:
      orig_cwd = os.getcwd()
      os.chdir(sandbox)
      try:
        for bad_dir in ("", "   ", "\t\n", None, 123, os.path.join(sandbox, "bad\x00dir")):
          with self.subTest(bad_dir=bad_dir):
            with self.assertRaises(ValueError):
              store.save_scar(bad_dir, rec, tier="project")  # type: ignore[arg-type]
        self.assertEqual(os.listdir(sandbox), [])

        existing_file = os.path.join(sandbox, "not_a_directory")
        with open(existing_file, "w", encoding="utf-8") as fh:
          fh.write("sentinel")
        with self.assertRaises(ValueError):
          store.save_scar(existing_file, rec, tier="project")
        with open(existing_file, "r", encoding="utf-8") as fh:
          self.assertEqual(fh.read(), "sentinel")
      finally:
        os.chdir(orig_cwd)

  def test_scar_config_store_error_paths(self):
    """Verifies ScarConfigStore rejects invalid paths, missing workspace_dir, and corrupt/non-dict JSON."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      registry = AgentPathRegistry(home_dir=fake_home)
      cfg_store = ScarConfigStore(registry=registry)

      for bad_dir in ("", "   ", None, "/tmp/bad\x00dir"):
        with self.subTest(bad_dir=bad_dir):
          with self.assertRaises(ValueError):
            cfg_store.add_scar_directory(
                bad_dir, scope="user-global", workspace_dir=ws_dir  # type: ignore[arg-type]
            )

      for bad_ws in (None, "", "   ", os.path.join(ws_dir, "bad\x00ws")):
        with self.subTest(bad_ws=bad_ws):
          with self.assertRaises(ValueError):
            cfg_store.add_scar_directory(
                "/valid/custom", scope="project-local", workspace_dir=bad_ws  # type: ignore[arg-type]
            )

      local_cfg_path = os.path.join(ws_dir, ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        fh.write("{corrupt json")
      with self.assertRaises(json.JSONDecodeError):
        cfg_store.add_scar_directory(
            "/valid/custom", scope="project-local", workspace_dir=ws_dir
        )

      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(["not", "a", "dict"], fh)
      with self.assertRaises(ValueError):
        cfg_store.add_scar_directory(
            "/valid/custom", scope="project-local", workspace_dir=ws_dir
        )


class TestUpstreamIssuePublisher(unittest.TestCase):
  """Tests gh auth preflight, integer-ID stripping, --approved gate, and --body-file submission."""

  def test_format_issue_payload_strips_assigned_integer_ids(self):
    """Verifies formatted upstream issue omits any assigned integer scar ID."""
    publisher = UpstreamIssuePublisher()
    self.assertEqual(publisher.repo, UPSTREAM_REPO)
    rec = _sample_record(
        title="SCAR-LOCAL-DB-0042: Guard Empty Database URLs",
        category="DB",
    )
    payload = publisher.format_issue_payload(rec)
    self.assertNotIn("SCAR-LOCAL-DB-0042", payload["title"])
    self.assertNotIn("SCAR-LOCAL-DB-0042", payload["body"])
    self.assertNotRegex(payload["title"], r"SCAR-(?:[A-Z0-9]+-)+\d+")
    self.assertNotRegex(payload["body"], r"SCAR-(?:[A-Z0-9]+-)+\d+")
    self.assertIn("Guard Empty Database URLs", payload["title"])
    self.assertIn("**Wound**:", payload["body"])
    self.assertIn("**Circuit Breaker**:", payload["body"])
    self.assertIn("**Socratic Probe**:", payload["body"])

  def test_check_access_handles_missing_gh_unauth_and_ready(self):
    """Verifies preflight handles missing gh binary, unauthenticated state, and full access."""
    publisher = UpstreamIssuePublisher()
    with mock.patch("shutil.which", return_value=None):
      res_missing = publisher.check_access()
      self.assertFalse(res_missing["ready"])
      self.assertFalse(res_missing["ghInstalled"])

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run",
        return_value=mock.MagicMock(returncode=1, stdout="", stderr="not logged in"),
    ):
      res_unauth = publisher.check_access()
      self.assertFalse(res_unauth["ready"])
      self.assertTrue(res_unauth["ghInstalled"])
      self.assertFalse(res_unauth["authenticated"])

    def _ok_subp(cmd, **kwargs):
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="Logged in", stderr="")
      if cmd[:3] == ["gh", "repo", "view"]:
        return mock.MagicMock(
            returncode=0,
            stdout='{"nameWithOwner":"hedges333/makeflow-skill"}',
            stderr="",
        )
      return mock.MagicMock(returncode=0, stdout="", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_ok_subp
    ):
      res_ready = publisher.check_access()
      self.assertTrue(res_ready["ready"])
      self.assertTrue(res_ready["authenticated"])
      self.assertTrue(res_ready["repoAccessible"])

  def test_submit_issue_requires_approval_and_uses_body_file(self):
    """Verifies unapproved submit is blocked and approved submit passes --body-file via temp dir."""
    publisher = UpstreamIssuePublisher()
    rec = _sample_record(title="SCAR-USER-DB-0009: Guard Empty URLs")

    with mock.patch("subprocess.run") as run_mock:
      with self.assertRaises(PermissionError):
        publisher.submit_issue(rec, approved=False)
      run_mock.assert_not_called()

    captured_body = {}

    def _mock_create(cmd, **kwargs):
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="Logged in", stderr="")
      if cmd[:3] == ["gh", "repo", "view"]:
        return mock.MagicMock(returncode=0, stdout='{"nameWithOwner":"hedges333/makeflow-skill"}', stderr="")
      if cmd[:3] == ["gh", "issue", "create"]:
        self.assertIn("--body-file", cmd)
        body_idx = cmd.index("--body-file") + 1
        body_path = cmd[body_idx]
        captured_body["path"] = body_path
        with open(body_path, "r", encoding="utf-8") as fh:
          captured_body["text"] = fh.read()
        return mock.MagicMock(
            returncode=0,
            stdout="https://github.com/hedges333/makeflow-skill/issues/999\n",
            stderr="",
        )
      return mock.MagicMock(returncode=1, stdout="", stderr="unexpected")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_mock_create
    ):
      result = publisher.submit_issue(rec, approved=True)

    self.assertTrue(result["submitted"])
    self.assertEqual(
        result["issueUrl"],
        "https://github.com/hedges333/makeflow-skill/issues/999",
    )
    self.assertNotIn("SCAR-USER-DB-0009", captured_body["text"])
    # Temp directory cleaned up automatically after submission (SCAR-TEST-45)
    self.assertFalse(os.path.exists(captured_body["path"]))

  def test_format_issue_payload_preserves_tier1_refs_and_rejects_id_only_title(self):
    """Verifies Tier 1 catalog references survive prose scrubbing while local IDs and ID-only titles fail/strip (BLOCKER-2)."""
    publisher = UpstreamIssuePublisher()
    rec = MinedScarRecord(
        title="SCAR-LOCAL-TEST-0042: Isolate Ephemeral Directories",
        category="TEST",
        wound="Tests leaked files under SCAR-LOCAL-TEST-0042: violating SCAR-TEST-45 and SCAR-ARCH-05.",
        trap="Callers ignored SCAR-SH-01 and SCAR-USER-TEST-0007: wrote to hardcoded paths.",
        circuit_breaker="If SCAR-CUSTOM-TEST-0003: path targets /tmp, halt per SCAR-TEST-45.",
        reflex="Use TemporaryDirectory under SCAR-TEST-45 and SCAR-ARCH-05.",
        socratic_probe="Did you follow SCAR-TEST-45 and SCAR-PROC-108?",
    )
    payload = publisher.format_issue_payload(rec)
    self.assertEqual(payload["title"], "[SCAR] [TEST] Isolate Ephemeral Directories")
    self.assertNotIn("SCAR-LOCAL-TEST-0042", payload["title"])
    self.assertNotIn("SCAR-LOCAL-TEST-0042", payload["body"])
    self.assertNotIn("SCAR-USER-TEST-0007", payload["body"])
    self.assertNotIn("SCAR-CUSTOM-TEST-0003", payload["body"])
    for tier1_ref in ("SCAR-TEST-45", "SCAR-ARCH-05", "SCAR-SH-01", "SCAR-PROC-108"):
      self.assertIn(tier1_ref, payload["body"])

    # Also strips a leading Tier 1 ID prefix on title
    prefixed_title_rec = _sample_record(title="SCAR-DB-0099 - Guard Empty URLs")
    prefixed_payload = publisher.format_issue_payload(prefixed_title_rec)
    self.assertEqual(prefixed_payload["title"], "[SCAR] [DB] Guard Empty URLs")

    # Rejects titles that become empty once assigned IDs are stripped
    for id_only in ("SCAR-LOCAL-DB-0042", "SCAR-DB-0001:", "SCAR-USER-ARCH-0003 - "):
      with self.subTest(id_only=id_only):
        with self.assertRaises(ValueError):
          publisher.format_issue_payload(_sample_record(title=id_only))

  def test_repo_validation_failure_paths_and_subprocess_timeouts(self):
    """Verifies owner/repo validation, timeout=30, TimeoutExpired/OSError handling, and gh failure paths (NBO #3)."""
    for bad_repo in ("-flag", "-owner/repo", "noslash", "a/b/c", "", "owner/bad repo"):
      with self.subTest(bad_repo=bad_repo):
        with self.assertRaises(ValueError):
          UpstreamIssuePublisher(repo=bad_repo)

    publisher = UpstreamIssuePublisher(repo="org_1.2/repo-3.4")
    self.assertEqual(publisher.repo, "org_1.2/repo-3.4")

    # gh repo view inaccessible + verify timeout=30 passed
    seen_timeouts = []

    def _repo_inaccessible(cmd, **kwargs):
      seen_timeouts.append(kwargs.get("timeout"))
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="ok", stderr="")
      return mock.MagicMock(returncode=1, stdout="", stderr="HTTP 404")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_repo_inaccessible
    ):
      res = publisher.check_access()
    self.assertFalse(res["ready"])
    self.assertTrue(res["authenticated"])
    self.assertFalse(res["repoAccessible"])
    self.assertEqual(seen_timeouts, [30, 30])

    # TimeoutExpired on gh auth status returns structured ready=False
    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["gh", "auth", "status"], timeout=30),
    ):
      res_timeout = publisher.check_access()
    self.assertFalse(res_timeout["ready"])
    self.assertFalse(res_timeout["authenticated"])

    # OSError on gh repo view returns structured ready=False
    def _repo_oserror(cmd, **kwargs):
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="ok", stderr="")
      raise OSError("exec error")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_repo_oserror
    ):
      res_oserr = publisher.check_access()
    self.assertFalse(res_oserr["ready"])
    self.assertTrue(res_oserr["authenticated"])
    self.assertFalse(res_oserr["repoAccessible"])

    # submit_issue rejects invalid scar before calling gh
    invalid_rec = MinedScarRecord(
        title="Bad Scar",
        category="DB",
        wound="Wound.",
        trap="Trap.",
        reflex="Reflex.",
        socratic_probe="Missing question mark.",
    )
    with mock.patch("subprocess.run") as run_mock:
      with self.assertRaises(ValueError):
        publisher.submit_issue(invalid_rec, approved=True)
      run_mock.assert_not_called()

    # submit_issue converts gh issue create non-zero exit into RuntimeError and passes timeout=30
    create_timeouts = []

    def _create_fails(cmd, **kwargs):
      create_timeouts.append(kwargs.get("timeout"))
      if cmd[:3] == ["gh", "issue", "create"]:
        return mock.MagicMock(returncode=1, stdout="", stderr="permission denied")
      return mock.MagicMock(returncode=0, stdout="ok", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_create_fails
    ):
      with self.assertRaises(RuntimeError):
        publisher.submit_issue(_sample_record(), approved=True)
    self.assertEqual(create_timeouts, [30, 30, 30])

    # submit_issue converts gh issue create TimeoutExpired/OSError into RuntimeError
    for exc in (
        subprocess.TimeoutExpired(cmd=["gh", "issue", "create"], timeout=30),
        OSError("spawn failed"),
    ):
      def _create_raises(cmd, _exc=exc, **kwargs):
        if cmd[:3] == ["gh", "issue", "create"]:
          raise _exc
        return mock.MagicMock(returncode=0, stdout="ok", stderr="")

      with self.subTest(exc=type(exc).__name__):
        with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
            "subprocess.run", side_effect=_create_raises
        ):
          with self.assertRaises(RuntimeError):
            publisher.submit_issue(_sample_record(), approved=True)


class TestScarMinerScriptSubcommands(unittest.TestCase):
  """Tests all five explicit BaseScript CLI subcommands (SCAR-API-0001)."""

  def test_all_five_subcommands_via_execute(self):
    """Verifies paths, save, save-config, upstream-check, and upstream-submit subcommands."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      custom_dir = os.path.join(fake_home, "my_custom_scars")

      # 1. paths subcommand
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=["paths", "--workspace", ws_dir, "--agent", "claude", "--home", fake_home],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      paths_data = json.loads(out.getvalue())
      self.assertEqual(paths_data["activeAgent"], "claude")
      self.assertEqual(
          paths_data["projectScarsDir"], os.path.join(ws_dir, ".workflow", "scars")
      )
      self.assertEqual(
          paths_data["userScarsDir"],
          os.path.join(fake_home, ".claude", "plugin_data", "makeflow", "scars"),
      )

      # 2. save subcommand (project tier)
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=[
              "save",
              "--tier",
              "project",
              "--workspace",
              ws_dir,
              "--home",
              fake_home,
              "--title",
              "Guard Empty URLs",
              "--category",
              "DB",
              "--wound",
              "An empty database URL opened an unintended local file.",
              "--trap",
              "Callers assumed configuration always returned a connection string.",
              "--circuit-breaker",
              "If URL is empty, stop before opening a handle.",
              "--reflex",
              "Validate the connection URL before opening a handle.",
              "--socratic-probe",
              "Did you validate the database URL before opening a handle?",
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      save_data = json.loads(out.getvalue())
      self.assertEqual(save_data["scarId"], "SCAR-LOCAL-DB-0001")
      self.assertTrue(os.path.isfile(save_data["filePath"]))

      # 3. save-config subcommand
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=[
              "save-config",
              "--dir",
              custom_dir,
              "--scope",
              "both",
              "--workspace",
              ws_dir,
              "--agent",
              "claude",
              "--home",
              fake_home,
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      cfg_res = json.loads(out.getvalue())
      self.assertEqual(cfg_res["scope"], "both")
      self.assertEqual(len(cfg_res["updatedFiles"]), 2)

      # 4. upstream-check subcommand (mocked ready)
      def _ok_gh(cmd, **kwargs):
        if cmd[:3] == ["gh", "issue", "create"]:
          return mock.MagicMock(
              returncode=0,
              stdout="https://github.com/hedges333/makeflow-skill/issues/777\n",
              stderr="",
          )
        return mock.MagicMock(returncode=0, stdout="ok", stderr="")

      with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
          "subprocess.run", side_effect=_ok_gh
      ):
        out, err = io.StringIO(), io.StringIO()
        rc = ScarMinerScript().execute(args=["upstream-check"], stdout=out, stderr=err)
        self.assertEqual(rc, 0, msg=err.getvalue())
        self.assertTrue(json.loads(out.getvalue())["ready"])

        # 5a. upstream-submit WITHOUT --approved fails with non-zero exit
        out_un, err_un = io.StringIO(), io.StringIO()
        rc_un = ScarMinerScript().execute(
            args=[
                "upstream-submit",
                "--title",
                "Guard Empty URLs",
                "--category",
                "DB",
                "--wound",
                "An empty database URL opened an unintended local file.",
                "--trap",
                "Callers assumed configuration always returned a connection string.",
                "--reflex",
                "Validate the connection URL before opening a handle.",
                "--socratic-probe",
                "Did you validate the database URL before opening a handle?",
            ],
            stdout=out_un,
            stderr=err_un,
        )
        self.assertNotEqual(rc_un, 0)
        self.assertIn("--approved", err_un.getvalue())

        # 5b. upstream-submit WITH --approved succeeds
        out_ok, err_ok = io.StringIO(), io.StringIO()
        rc_ok = ScarMinerScript().execute(
            args=[
                "upstream-submit",
                "--approved",
                "--title",
                "SCAR-LOCAL-DB-0001: Guard Empty URLs",
                "--category",
                "DB",
                "--wound",
                "An empty database URL opened an unintended local file.",
                "--trap",
                "Callers assumed configuration always returned a connection string.",
                "--reflex",
                "Validate the connection URL before opening a handle.",
                "--socratic-probe",
                "Did you validate the database URL before opening a handle?",
            ],
            stdout=out_ok,
            stderr=err_ok,
        )
        self.assertEqual(rc_ok, 0, msg=err_ok.getvalue())
        sub_res = json.loads(out_ok.getvalue())
        self.assertTrue(sub_res["submitted"])
        self.assertNotIn("SCAR-LOCAL-DB-0001", sub_res["title"])

  def test_cli_json_file_snake_camel_overrides_and_non_object_rejection(self):
    """Verifies --json-file supports snake_case and camelCase, CLI overrides, and rejects non-objects."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      snake_path = os.path.join(fake_home, "snake.json")
      with open(snake_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "title": "Snake Case Scar",
                "category": "DB",
                "wound": "An empty database URL opened an unintended local file.",
                "trap": "Callers assumed configuration always returned a connection string.",
                "circuit_breaker": "If URL is empty, halt before connecting.",
                "permanent_reflex": "Validate the connection URL before opening a handle.",
                "socratic_probe": "Did you validate the database URL before connecting?",
                "applies_when": "Target 30: tdd",
            },
            fh,
        )

      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=["save", "--tier", "project", "--workspace", ws_dir, "--json-file", snake_path],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      snake_res = json.loads(out.getvalue())
      self.assertEqual(snake_res["scarId"], "SCAR-LOCAL-DB-0001")

      camel_path = os.path.join(fake_home, "camel.json")
      with open(camel_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "title": "Original Camel Title",
                "category": "TEST",
                "wound": "Tests wrote files into the workspace root.",
                "trap": "Tests skipped temporary directories.",
                "circuitBreaker": "If writing test files, stop before using CWD.",
                "permanentReflex": "Use TemporaryDirectory for every test file write.",
                "socraticProbe": "Does this test use TemporaryDirectory?",
                "appliesWhen": "Target 30: tdd",
            },
            fh,
        )
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=[
              "save",
              "--tier",
              "project",
              "--workspace",
              ws_dir,
              "--json-file",
              camel_path,
              "--title",
              "CLI Override Title",
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      camel_res = json.loads(out.getvalue())
      parsed = MarkdownScarStore().parse_markdown_file(camel_res["filePath"])
      self.assertEqual(parsed[0]["title"], "CLI Override Title")
      self.assertEqual(parsed[0]["applies_when"], "Target 30: tdd")

      bad_json_path = os.path.join(fake_home, "array.json")
      with open(bad_json_path, "w", encoding="utf-8") as fh:
        json.dump(["not", "an", "object"], fh)
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=["save", "--tier", "project", "--workspace", ws_dir, "--json-file", bad_json_path],
          stdout=out,
          stderr=err,
      )
      self.assertNotEqual(rc, 0)
      self.assertIn("JSON object", err.getvalue())

  def test_cli_save_user_tier_custom_tier_and_missing_custom_dir(self):
    """Verifies --tier user, --tier custom, and missing --custom-dir error handling."""
    with tempfile.TemporaryDirectory() as fake_home:
      custom_dir = os.path.join(fake_home, "team_scars")
      common_flags = [
          "--title",
          "Guard Empty URLs",
          "--category",
          "PROC",
          "--wound",
          "An empty database URL opened an unintended local file.",
          "--trap",
          "Callers assumed configuration always returned a connection string.",
          "--reflex",
          "Validate the connection URL before opening a handle.",
          "--socratic-probe",
          "Did you validate the database URL before opening a handle?",
      ]

      # --tier user saves into active agent scars_dir
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=["save", "--tier", "user", "--agent", "codex", "--home", fake_home] + common_flags,
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      user_res = json.loads(out.getvalue())
      self.assertEqual(user_res["scarId"], "SCAR-USER-PROC-0001")
      self.assertEqual(
          user_res["targetDir"],
          os.path.join(fake_home, ".codex", "plugin_data", "makeflow", "scars"),
      )

      # --tier custom with --custom-dir saves into custom_dir
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=["save", "--tier", "custom", "--custom-dir", custom_dir] + common_flags,
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      custom_res = json.loads(out.getvalue())
      self.assertEqual(custom_res["scarId"], "SCAR-CUSTOM-PROC-0001")
      self.assertEqual(custom_res["targetDir"], custom_dir)

      # --tier custom without --custom-dir fails cleanly
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=["save", "--tier", "custom"] + common_flags,
          stdout=out,
          stderr=err,
      )
      self.assertNotEqual(rc, 0)
      self.assertIn("--custom-dir", err.getvalue())

  def test_cli_upstream_check_returns_exit_code_1_when_not_ready(self):
    """Verifies upstream-check CLI returns exit code 1 when gh access is not ready."""
    with mock.patch("shutil.which", return_value=None):
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(args=["upstream-check"], stdout=out, stderr=err)
    self.assertEqual(rc, 1)
    payload = json.loads(out.getvalue())
    self.assertFalse(payload["ready"])
    self.assertFalse(payload["ghInstalled"])


if __name__ == "__main__":
  unittest.main()

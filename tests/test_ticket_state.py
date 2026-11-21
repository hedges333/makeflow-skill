#!/usr/bin/env python3
"""Hermetic unit tests for structured JSON ticket state persistence (GH-240)."""

from dataclasses import FrozenInstanceError
import json
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.ticket_state import (
    TICKET_JSON_FILENAME,
    TicketMetadata,
    TicketStateStore,
)


class TestTicketMetadata(unittest.TestCase):
  """Tests for the immutable TicketMetadata value object."""

  def test_constants_and_defaults(self) -> None:
    """Verifies canonical filename constant and empty defaults."""
    self.assertEqual(TICKET_JSON_FILENAME, "10_ticket.json")
    meta = TicketMetadata()
    self.assertEqual(meta.ticket_id, "")
    self.assertEqual(meta.title, "")
    self.assertEqual(meta.url, "")
    self.assertEqual(meta.branch, "")
    self.assertEqual(meta.labels, ())
    self.assertEqual(meta.source, "")

  def test_metadata_is_frozen(self) -> None:
    """Verifies TicketMetadata is immutable."""
    meta = TicketMetadata(ticket_id="GH-240", title="[BUG] terminal title formatting")
    with self.assertRaises(FrozenInstanceError):
      meta.title = "mutated"  # type: ignore[misc]

  def test_from_dict_canonical_keys_and_round_trip(self) -> None:
    """Verifies canonical JSON dict normalization and to_dict round-trip."""
    payload = {
        "ticket_id": "  GH-240  ",
        "title": "  [BUG] terminal title formatting  ",
        "url": "https://github.com/hedges333/makeflow-skill/issues/240",
        "branch": "fix/240-terminal-title-formatting",
        "labels": ["bug", "P1"],
        "source": "github",
    }
    meta = TicketMetadata.from_dict(payload)
    self.assertEqual(meta.ticket_id, "GH-240")
    self.assertEqual(meta.title, "[BUG] terminal title formatting")
    self.assertEqual(
        meta.url, "https://github.com/hedges333/makeflow-skill/issues/240"
    )
    self.assertEqual(meta.branch, "fix/240-terminal-title-formatting")
    self.assertEqual(meta.labels, ("bug", "P1"))
    self.assertEqual(meta.source, "github")

    as_dict = meta.to_dict()
    self.assertIsInstance(as_dict["labels"], list)
    self.assertEqual(
        as_dict,
        {
            "ticket_id": "GH-240",
            "title": "[BUG] terminal title formatting",
            "url": "https://github.com/hedges333/makeflow-skill/issues/240",
            "branch": "fix/240-terminal-title-formatting",
            "labels": ["bug", "P1"],
            "source": "github",
        },
    )
    self.assertEqual(TicketMetadata.from_dict(as_dict), meta)

  def test_from_dict_raw_gh_issue_json(self) -> None:
    """Verifies raw gh issue view --json payload normalizes without regexes."""
    gh_payload = {
        "number": 240,
        "title": "[BUG] terminal title formatting",
        "url": "https://github.com/hedges333/makeflow-skill/issues/240",
        "assignees": [],
        "labels": [
            {"id": "L1", "name": "bug", "color": "d73a4a"},
            {"id": "L2", "name": "in-progress"},
            {"name": ""},
            "documentation",
        ],
    }
    meta = TicketMetadata.from_dict(gh_payload)
    self.assertEqual(meta.ticket_id, "GH-240")
    self.assertEqual(meta.title, "[BUG] terminal title formatting")
    self.assertEqual(
        meta.url, "https://github.com/hedges333/makeflow-skill/issues/240"
    )
    self.assertEqual(meta.labels, ("bug", "in-progress", "documentation"))

  def test_from_dict_local_ticket_schema(self) -> None:
    """Verifies .workflow/tickets/LOCAL-<N>.json id field normalization."""
    local_payload = {
        "id": "LOCAL-42",
        "title": "Implement offline state sync",
        "status": "OPEN",
        "priority": "P1",
        "labels": ["enhancement", "offline"],
        "blocked_by": [],
        "body": "Detailed local body.",
    }
    meta = TicketMetadata.from_dict(local_payload)
    self.assertEqual(meta.ticket_id, "LOCAL-42")
    self.assertEqual(meta.title, "Implement offline state sync")
    self.assertEqual(meta.url, "")
    self.assertEqual(meta.labels, ("enhancement", "offline"))

  def test_from_dict_ticket_id_precedence_and_invalid_number(self) -> None:
    """Verifies ticket_id > id > number precedence and rejects bool/non-positive numbers."""
    meta_both = TicketMetadata.from_dict(
        {"ticket_id": "GH-99", "id": "LOCAL-1", "number": 240}
    )
    self.assertEqual(meta_both.ticket_id, "GH-99")

    meta_id = TicketMetadata.from_dict({"id": "LOCAL-7", "number": 240})
    self.assertEqual(meta_id.ticket_id, "LOCAL-7")

    for bad_num in (True, False, 0, -5, "abc", None):
      meta_bad = TicketMetadata.from_dict({"number": bad_num})
      self.assertEqual(meta_bad.ticket_id, "")

  def test_from_dict_rejects_non_dict(self) -> None:
    """Verifies from_dict rejects non-dict inputs loudly."""
    for bad in (None, [], "GH-240", 123):
      with self.assertRaises(TypeError):
        TicketMetadata.from_dict(bad)  # type: ignore[arg-type]

  def test_has_distinct_title(self) -> None:
    """Verifies has_distinct_title rejects empty and ticket-ID-identical titles."""
    self.assertTrue(
        TicketMetadata(
            ticket_id="GH-240", title="[BUG] terminal title formatting"
        ).has_distinct_title()
    )
    self.assertTrue(
        TicketMetadata(ticket_id="", title="Standalone task").has_distinct_title()
    )
    self.assertFalse(
        TicketMetadata(ticket_id="GH-233", title="").has_distinct_title()
    )
    self.assertFalse(
        TicketMetadata(ticket_id="GH-233", title="   ").has_distinct_title()
    )
    self.assertFalse(
        TicketMetadata(ticket_id="GH-233", title="GH-233").has_distinct_title()
    )
    self.assertFalse(
        TicketMetadata(
            ticket_id="GH-233", title="  gh-233  "
        ).has_distinct_title()
    )


class TestTicketStateStore(unittest.TestCase):
  """Tests for TicketStateStore persistence and error resilience."""

  def setUp(self) -> None:
    self._tmp = tempfile.TemporaryDirectory(prefix="makeflow_ticket_state_")
    self.addCleanup(self._tmp.cleanup)
    self.repo_root = self._tmp.name

  def test_from_repo_root_save_exists_and_load(self) -> None:
    """Verifies from_repo_root creates state_dir, writes deterministic JSON, and reloads."""
    store = TicketStateStore.from_repo_root(self.repo_root)
    expected_path = os.path.join(
        self.repo_root, ".workflow", "state", TICKET_JSON_FILENAME
    )
    self.assertEqual(store.path, expected_path)
    self.assertFalse(store.exists())
    self.assertIsNone(store.load())

    meta = TicketMetadata(
        ticket_id="GH-240",
        title="[BUG] terminal title formatting",
        url="https://github.com/hedges333/makeflow-skill/issues/240",
        branch="fix/240-terminal-title-formatting",
        labels=("bug",),
        source="github",
    )
    written_path = store.save(meta)
    self.assertEqual(written_path, expected_path)
    self.assertTrue(store.exists())

    with open(expected_path, "r", encoding="utf-8") as f:
      raw_text = f.read()
    self.assertTrue(raw_text.endswith("\n"))
    self.assertIn('\n  "ticket_id": "GH-240"', raw_text)

    loaded = store.load()
    self.assertEqual(loaded, meta)

  def test_save_accepts_raw_gh_dict(self) -> None:
    """Verifies save() normalizes raw dict payloads before writing."""
    state_dir = os.path.join(self.repo_root, ".workflow", "state")
    store = TicketStateStore(state_dir)
    store.save(
        {
            "number": 240,
            "title": "[BUG] terminal title formatting",
            "url": "https://github.com/hedges333/makeflow-skill/issues/240",
            "labels": [{"name": "bug"}],
            "source": "github",
        }
    )
    loaded = store.load()
    self.assertIsNotNone(loaded)
    assert loaded is not None
    self.assertEqual(loaded.ticket_id, "GH-240")
    self.assertEqual(loaded.title, "[BUG] terminal title formatting")
    self.assertEqual(loaded.labels, ("bug",))
    self.assertEqual(loaded.source, "github")

  def test_load_returns_none_on_corrupt_non_utf8_and_non_dict_json(self) -> None:
    """Verifies load() returns None cleanly for malformed or unexpected JSON files."""
    state_dir = os.path.join(self.repo_root, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    store = TicketStateStore(state_dir)

    # 1. Corrupt JSON syntax
    with open(store.path, "w", encoding="utf-8") as f:
      f.write("{invalid json")
    self.assertIsNone(store.load())

    # 2. Non-UTF8 binary payload
    with open(store.path, "wb") as f:
      f.write(b"\xff\xfe\x00\x01")
    self.assertIsNone(store.load())

    # 3. Valid JSON that is not a dict
    for payload in ("[1, 2, 3]", '"GH-240"', "42", "null"):
      with open(store.path, "w", encoding="utf-8") as f:
        f.write(payload)
      self.assertIsNone(store.load(), f"Expected None for payload {payload!r}")

  def test_ticket_state_module_has_no_shebang_or_regex_import(self) -> None:
    """Verifies SCAR-ARCH-05 (no shebang in lib/) and zero regexes in lib/ticket_state.py."""
    mod_path = os.path.join(_REPO_ROOT, "lib", "ticket_state.py")
    with open(mod_path, "r", encoding="utf-8") as f:
      source = f.read()
    self.assertFalse(source.startswith("#!"))
    self.assertNotIn("import re", source)


if __name__ == "__main__":
  unittest.main()

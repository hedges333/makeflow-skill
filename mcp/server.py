#!/usr/bin/env python3
"""Makefile Workflow MCP Server.

Implements Model Context Protocol (MCP) JSON-RPC 2.0 over standard input/output.
Provides state queries, DAG transition checks, Git tree checks, safety checks,
and user configuration lookup.
"""

import copy
import datetime
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)

try:
  from sync_synthetic_scars import query_scars, resolve_scars_db_path
except ImportError:
  query_scars = None
  resolve_scars_db_path = None

DB_TOOLS = (
    "psql",
    "pg_dump",
    "pg_restore",
    "mysql",
    "mysqldump",
    "mariadb",
    "mongo",
    "mongosh",
    "redis-cli",
    "sqlite3",
    "sqlcmd",
    "flyway",
    "liquibase",
    "alembic",
    "prisma",
    "goose",
    "dbmate",
)

PROD_TOKEN_RE = re.compile(
    r"(?:^|[^a-zA-Z0-9])(prod|production)(?:$|[^a-zA-Z0-9])",
    re.IGNORECASE,
)

URI_USERINFO_RE = re.compile(r"(://)[^/\s'\";@]+@")

HOST_VALUE_RE = re.compile(
    r"(?:"
    r"(?:^|\s)-(?:h|S)\s*['\"]?|"
    r"--host(?:=|\s+)['\"]?|"
    r"\b(?:DB_HOST|PGHOST|MYSQL_HOST|MARIADB_HOST|REDIS_HOST|MONGO_HOST|HOST)=['\"]?|"
    r"://|"
    r"@"
    r")([a-zA-Z0-9._-]+)",
    re.IGNORECASE,
)

QUOTED_ARG_PAT = r"(?:'[^']*'|\"(?:\\.|[^\"\\])*\")"

INLINE_QUERY_RE = re.compile(
    rf"(?:(?<=^)|(?<=\s))(?:-c|-e|--command|--execute|--eval)(?:=|\s*){QUOTED_ARG_PAT}",
    re.IGNORECASE,
)

SQLITE_CMD_RE = re.compile(
    rf"(\bsqlite3\b[^\n]*?\s){QUOTED_ARG_PAT}\s*$",
    re.IGNORECASE,
)

ALL_TARGETS = {
    "T00",
    "T10",
    "T15",
    "T20",
    "T30",
    "T40",
    "T42",
    "T45",
    "T50",
    "T60",
    "T70",
    "T75",
    "T80",
    "T90",
}

VALID_TRANSITIONS = {
    "T00": ALL_TARGETS - {"T00"},
    "T10": {"T15"},
    "T15": {"T20"},
    "T20": {"T30"},
    "T30": {"T40", "T20"},
    "T40": {"T42", "T45"},
    "T42": {"T30"},
    "T45": {"T50"},
    "T50": {"T60"},
    "T60": {"T70", "T40"},
    "T70": {"T75", "T30"},
    "T75": {"T80"},
    "T80": {"T90"},
    "T90": set(),
}

GATE_EVIDENCE_KEYS = {
    ("T20", "T30"): "humanPlanApproved",
    ("T42", "T30"): "humanBlockerTriageApproved",
    ("T45", "T50"): "humanReviewApproved",
    ("T50", "T60"): "humanCommitApproved",
    ("T60", "T70"): "humanPushApproved",
    ("T75", "T80"): "humanLandingApproved",
}

REQUIRED_STATE_CARDS = {
    ("T10", "T15"): "10_ticket.md",
    ("T15", "T20"): "15_archaeology_receipt.md",
    ("T20", "T30"): "20_plan_approved.md",
    ("T30", "T40"): "20_plan_approved.md",
    ("T40", "T45"): "40_critic_review.md",
    ("T40", "T42"): "40_critic_review.md",
    ("T45", "T50"): "45_review_approved.md",
    ("T70", "T75"): "70_bot_triage.md",
}


def is_plugin_dir(path):
  """Checks whether a directory is inside a makeflow plugin installation root."""
  if not path or not isinstance(path, str):
    return False
  norm_path = os.path.abspath(os.path.expanduser(path))
  known_plugin_prefixes = (
      os.path.abspath(os.path.expanduser("~/.gemini/config/plugins")),
      os.path.abspath(os.path.expanduser("~/.gemini/plugins")),
      os.path.abspath(os.path.expanduser("~/.claude/plugins")),
  )
  for prefix in known_plugin_prefixes:
    if norm_path == prefix or norm_path.startswith(prefix + os.sep):
      return True

  curr = norm_path
  while curr and curr != os.path.dirname(curr):
    if os.path.isdir(os.path.join(curr, ".git")):
      return False
    plugin_json_path = os.path.join(curr, "plugin.json")
    if os.path.isfile(plugin_json_path):
      try:
        with open(plugin_json_path, "r", encoding="utf-8") as f:
          data = json.load(f)
        if isinstance(data, dict) and data.get("name") == "makeflow":
          return True
      except Exception:
        pass
    curr = os.path.dirname(curr)
  return False


def resolve_workspace_dir(arguments):
  ws = (arguments or {}).get("workspaceDir")
  if ws:
    return os.path.expanduser(ws)
  env_ws = os.getenv("MAKEFILE_WORKFLOW_WORKSPACE")
  if env_ws:
    return os.path.expanduser(env_ws)
  if is_plugin_dir(os.getcwd()):
    return None
  return os.getcwd()


def map_to_agy_tier(model_value):
  """Maps a concrete model identifier to an Antigravity subagent tier."""
  if not model_value:
    return "inherit"
  val = str(model_value).strip().lower()
  if val in {"inherit", "flash_lite", "flash", "pro"}:
    return val
  tokens = set(re.split(r"[-_./\s]+", val))
  if (
      "flash-lite" in val
      or "flash_lite" in val
      or any(tok in tokens for tok in ("lite", "haiku", "mini", "nano"))
  ):
    return "flash_lite"
  if any(tok in tokens or tok in val for tok in ("pro", "argon", "opus", "high", "o1", "o3")):
    return "pro"
  if any(tok in tokens or tok in val for tok in ("flash", "fast", "low")):
    return "flash"
  return "inherit"


def _normalize_str_list(items):
  if not isinstance(items, list):
    return []
  return [x.strip() for x in items if isinstance(x, str) and x.strip()]


def _parse_csv_env(env_name):
  raw = os.getenv(env_name, "")
  if not raw:
    return []
  return [item.strip() for item in raw.split(",") if item.strip()]


def deep_merge(base, override):
  """Recursively merges override dictionary into base dictionary without mutating base."""
  result = copy.deepcopy(base) if isinstance(base, dict) else {}
  if not isinstance(override, dict):
    return result
  for key, val in override.items():
    if key in result and isinstance(result[key], dict) and isinstance(val, dict):
      result[key] = deep_merge(result[key], val)
    else:
      result[key] = copy.deepcopy(val)
  return result


def load_default_config():
  """Loads the canonical default_config.json from repository or install root."""
  candidates = [
      os.path.join(
          os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
          "default_config.json",
      ),
      os.path.join(os.getcwd(), "default_config.json"),
      os.path.expanduser(
          "~/.gemini/config/plugins/makeflow/default_config.json"
      ),
  ]
  for path in candidates:
    if os.path.isfile(path):
      try:
        with open(path, "r", encoding="utf-8") as f:
          data = json.load(f)
        if isinstance(data, dict):
          return data
      except Exception:
        pass
  return {
      "models": {
          "adversarial-critic": "inherit",
          "archaeology-worker": "inherit",
          "tdd-worker": "inherit",
          "triage-worker": "inherit",
      },
      "review": {
          "maxReviewRounds": 3,
          "strictSixPillars": True,
      },
      "ci": {
          "pollIntervalSeconds": 30,
          "quiescenceSeconds": 180,
      },
      "database": {
          "prodDatabases": [],
          "testDatabases": [],
          "prodHosts": [],
      },
      "state": {
          "completedCardAction": "unlink",
      },
  }


def resolve_workflow_config(workspace_dir=None):
  """Resolves workflow configuration from workspace, global path, or environment."""
  ws = workspace_dir if workspace_dir is not None else resolve_workspace_dir({})
  local_cfg_path = os.path.join(ws, ".workflow", "config.json") if ws else None
  global_cfg_path = os.path.expanduser("~/.gemini/config/makeflow.json")

  config = load_default_config()
  config["configSource"] = "defaults"
  config["envOverrides"] = []

  candidates = [p for p in (local_cfg_path, global_cfg_path) if p]
  for cfg_path in candidates:
    if os.path.isfile(cfg_path):
      try:
        with open(cfg_path, "r", encoding="utf-8") as f:
          loaded = json.load(f)
        if isinstance(loaded, dict):
          config = deep_merge(config, loaded)
          if "database" in config and isinstance(config["database"], dict):
            for db_key in ("prodDatabases", "testDatabases", "prodHosts"):
              if db_key in config["database"]:
                config["database"][db_key] = _normalize_str_list(
                    config["database"][db_key]
                )
          config["configSource"] = cfg_path
          break
      except Exception:
        pass

  env_mappings = {
      "MAKEFILE_WORKFLOW_CRITIC_MODEL": "adversarial-critic",
      "MAKEFILE_WORKFLOW_WORKER_MODEL": "archaeology-worker",
      "MAKEFILE_WORKFLOW_ARCHAEOLOGY_MODEL": "archaeology-worker",
      "MAKEFILE_WORKFLOW_TDD_MODEL": "tdd-worker",
      "MAKEFILE_WORKFLOW_TRIAGE_MODEL": "triage-worker",
  }
  for env_var, agent_key in env_mappings.items():
    val = os.getenv(env_var)
    if val:
      config["models"][agent_key] = val.strip()
      config["envOverrides"].append(f"{env_var}={val.strip()}")

  db_env_mappings = {
      "MAKEFILE_WORKFLOW_PROD_DBS": "prodDatabases",
      "MAKEFILE_WORKFLOW_TEST_DBS": "testDatabases",
      "MAKEFILE_WORKFLOW_PROD_HOSTS": "prodHosts",
  }
  for env_var, db_key in db_env_mappings.items():
    vals = _parse_csv_env(env_var)
    if vals:
      merged = list(config["database"][db_key])
      for v in vals:
        if v not in merged:
          merged.append(v)
      config["database"][db_key] = merged
      config["envOverrides"].append(f"{env_var}={','.join(vals)}")

  ci_env_mappings = {
      "MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS": "quiescenceSeconds",
      "MAKEFILE_WORKFLOW_CI_POLL_INTERVAL": "pollIntervalSeconds",
  }
  for env_var, ci_key in ci_env_mappings.items():
    val = os.getenv(env_var)
    if val and val.strip().isdigit():
      num = int(val.strip())
      if num > 0:
        config["ci"][ci_key] = num
        config["envOverrides"].append(f"{env_var}={num}")

  if "state" not in config or not isinstance(config["state"], dict):
    config["state"] = {"completedCardAction": "unlink"}
  else:
    action = config["state"].get("completedCardAction", "unlink")
    if action not in ("unlink", "archive"):
      action = "unlink"
    config["state"]["completedCardAction"] = action

  env_card_action = os.getenv("MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION")
  if env_card_action and env_card_action.strip() in ("unlink", "archive"):
    config["state"]["completedCardAction"] = env_card_action.strip()
    config["envOverrides"].append(
        f"MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION={env_card_action.strip()}"
    )

  config["agyModelTiers"] = {
      role: map_to_agy_tier(val) for role, val in config["models"].items()
  }
  return config


def is_database_command(cmd):
  if "DATABASE_URL" in cmd or "DB_HOST" in cmd:
    return True
  for tool in DB_TOOLS:
    if re.search(rf"(?:^|[^a-zA-Z0-9_-]){re.escape(tool)}(?:$|[^a-zA-Z0-9_-])", cmd):
      return True
  return False


def _preprocess_shell(cmd):
  """Strips unquoted # comments and normalizes ANSI-C $'...' literals."""
  out = []
  quote = None
  escaped = False
  i = 0
  n = len(cmd)
  while i < n:
    ch = cmd[i]
    if quote is None:
      if escaped:
        out.append("\\")
        out.append(ch)
        escaped = False
        i += 1
        continue
      if ch == "\\":
        escaped = True
        i += 1
        continue
      if ch == "#" and (i == 0 or cmd[i - 1] in " \t\r\n;|&()"):
        while i < n and cmd[i] != "\n":
          i += 1
        continue
      if cmd[i : i + 2] == "$'":
        j = i + 2
        body = []
        closed = False
        while j < n:
          c2 = cmd[j]
          if c2 == "\\" and j + 1 < n:
            body.append(cmd[j + 1])
            j += 2
            continue
          if c2 == "'":
            closed = True
            j += 1
            break
          body.append(c2)
          j += 1
        if closed:
          safe_single = "'" + "".join(body).replace("'", "'\\''") + "'"
          out.append(safe_single)
          i = j
          continue
        out.append("'")
        i += 2
        continue
      if ch in ("'", '"'):
        quote = ch
        out.append(ch)
        i += 1
        continue
      out.append(ch)
      i += 1
      continue

    if quote == "'":
      out.append(ch)
      if ch == "'":
        quote = None
      i += 1
      continue

    if escaped:
      out.append("\\")
      out.append(ch)
      escaped = False
      i += 1
      continue
    if ch == "\\":
      escaped = True
      i += 1
      continue
    out.append(ch)
    if ch == '"':
      quote = None
    i += 1

  if escaped:
    out.append("\\")
  return "".join(out)


def _strip_shell_redirections(seg):
  """Strips unquoted shell redirections (such as 2>&1, 2>/dev/null, > out)."""
  out = []
  quote = None
  escaped = False
  i = 0
  n = len(seg)
  while i < n:
    ch = seg[i]
    if escaped:
      out.append("\\")
      out.append(ch)
      escaped = False
      i += 1
      continue
    if ch == "\\" and quote != "'":
      escaped = True
      i += 1
      continue
    if quote:
      out.append(ch)
      if ch == quote:
        quote = None
      i += 1
      continue
    if ch in ("'", '"'):
      quote = ch
      out.append(ch)
      i += 1
      continue

    op_idx = -1
    if ch.isdigit() and (i == 0 or seg[i - 1] in " \t\r\n;|&()"):
      k = i
      while k < n and seg[k].isdigit():
        k += 1
      if k < n and seg[k] in ("<", ">"):
        op_idx = k
    elif ch in ("<", ">") or seg[i : i + 2] == "&>":
      op_idx = i

    if op_idx != -1:
      if seg[op_idx : op_idx + 3] in ("&>>", "<<<", "<<-"):
        j = op_idx + 3
      elif seg[op_idx : op_idx + 2] in ("&>", ">>", "<<", "<&", ">&", "<>", ">|"):
        j = op_idx + 2
      else:
        j = op_idx + 1
      while j < n and seg[j] in (" ", "\t"):
        j += 1
      w_quote = None
      w_esc = False
      w_paren = 0
      while j < n:
        wc = seg[j]
        if w_esc:
          w_esc = False
          j += 1
          continue
        if wc == "\\" and w_quote != "'":
          w_esc = True
          j += 1
          continue
        if w_quote:
          if wc == w_quote:
            w_quote = None
          j += 1
          continue
        if wc in ("'", '"'):
          w_quote = wc
          j += 1
          continue
        if wc == "(":
          w_paren += 1
          j += 1
          continue
        if wc == ")":
          if w_paren > 0:
            w_paren -= 1
            j += 1
            continue
          break
        if w_paren == 0 and wc in (" ", "\t", "\r", "\n", ";", "|", "&", "<", ">"):
          break
        j += 1
      out.append(" ")
      i = j
      continue

    out.append(ch)
    i += 1

  if escaped:
    out.append("\\")
  return "".join(out)


def _split_shell_subcommands(cmd):
  """Splits a shell command string on unquoted |, ||, &&, ;, and newline."""
  parts = []
  buf = []
  quote = None
  escaped = False
  i = 0
  n = len(cmd)
  while i < n:
    ch = cmd[i]
    if escaped:
      buf.append(ch)
      escaped = False
      i += 1
      continue
    if ch == "\\" and quote != "'":
      buf.append(ch)
      escaped = True
      i += 1
      continue
    if quote:
      buf.append(ch)
      if ch == quote:
        quote = None
      i += 1
      continue
    if ch in ("'", '"'):
      quote = ch
      buf.append(ch)
      i += 1
      continue
    if ch in (";", "\n"):
      seg = "".join(buf).strip()
      if seg:
        parts.append(seg)
      buf = []
      i += 1
      continue
    if ch == "|":
      seg = "".join(buf).strip()
      if seg:
        parts.append(seg)
      buf = []
      if i + 1 < n and cmd[i + 1] == "|":
        i += 2
      else:
        i += 1
      continue
    if ch == "&":
      if i + 1 < n and cmd[i + 1] == "&":
        seg = "".join(buf).strip()
        if seg:
          parts.append(seg)
        buf = []
        i += 2
        continue
      if (i > 0 and cmd[i - 1] in ("<", ">")) or (i + 1 < n and cmd[i + 1] == ">"):
        buf.append(ch)
        i += 1
        continue
      seg = "".join(buf).strip()
      if seg:
        parts.append(seg)
      buf = []
      i += 1
      continue
    buf.append(ch)
    i += 1
  tail = "".join(buf).strip()
  if tail:
    parts.append(tail)
  return parts or [cmd]


def _extract_shell_substitutions(cmd):
  """Extracts unescaped $(...), `...`, and unquoted (...) subshell bodies."""
  results = []
  in_single = False
  in_double = False
  escaped = False
  i = 0
  n = len(cmd)
  while i < n:
    ch = cmd[i]
    if escaped:
      escaped = False
      i += 1
      continue
    if ch == "\\" and not in_single:
      escaped = True
      i += 1
      continue
    if ch == "'" and not in_double:
      in_single = not in_single
      i += 1
      continue
    if ch == '"' and not in_single:
      in_double = not in_double
      i += 1
      continue
    if not in_single:
      if cmd[i : i + 2] == "$(":
        depth = 1
        j = i + 2
        sub_single = False
        sub_double = False
        sub_esc = False
        while j < n and depth > 0:
          c2 = cmd[j]
          if sub_esc:
            sub_esc = False
          elif c2 == "\\" and not sub_single:
            sub_esc = True
          elif c2 == "'" and not sub_double:
            sub_single = not sub_single
          elif c2 == '"' and not sub_single:
            sub_double = not sub_double
          elif not sub_single and not sub_double:
            if c2 == "(":
              depth += 1
            elif c2 == ")":
              depth -= 1
          j += 1
        inner = cmd[i + 2 : j - 1 if depth == 0 else j].strip()
        if inner:
          results.append(inner)
        i = j
        continue
      if ch == "`":
        j = i + 1
        sub_esc = False
        while j < n:
          c2 = cmd[j]
          if sub_esc:
            sub_esc = False
          elif c2 == "\\":
            sub_esc = True
          elif c2 == "`":
            break
          j += 1
        inner = cmd[i + 1 : j].strip()
        if inner:
          results.append(inner)
        i = j + 1
        continue
      if not in_double and ch == "(":
        depth = 1
        j = i + 1
        sub_single = False
        sub_double = False
        sub_esc = False
        while j < n and depth > 0:
          c2 = cmd[j]
          if sub_esc:
            sub_esc = False
          elif c2 == "\\" and not sub_single:
            sub_esc = True
          elif c2 == "'" and not sub_double:
            sub_single = not sub_single
          elif c2 == '"' and not sub_single:
            sub_double = not sub_double
          elif not sub_single and not sub_double:
            if c2 == "(":
              depth += 1
            elif c2 == ")":
              depth -= 1
          j += 1
        inner = cmd[i + 1 : j - 1 if depth == 0 else j].strip()
        if inner:
          results.append(inner)
        i = j
        continue
    i += 1
  return results


def _expand_shell_subcommands(cmd, max_depth=3):
  """Expands pipelines, chains, command substitutions, subshells, and sh -c."""
  expanded = []
  queue = [(cmd, 0)]
  while queue:
    raw_current, depth = queue.pop(0)
    current = _preprocess_shell(raw_current)
    if depth < max_depth:
      for inner in _extract_shell_substitutions(current):
        queue.append((inner, depth + 1))
    for raw_seg in _split_shell_subcommands(current):
      seg = _strip_shell_redirections(raw_seg).strip()
      if not seg:
        continue
      expanded.append(seg)
      if depth < max_depth:
        for tokens in _tokenize_subcommand(seg):
          exe, rem = _unwrap_command_tokens(tokens)
          if exe == "SHELL_C" and rem:
            queue.append((rem[0], depth + 1))
  return expanded or [cmd]


def _contains_identifier(cmd, name):
  sep = r"[^a-zA-Z0-9_-]"
  pattern = rf"(?:^|{sep}){re.escape(name)}(?:$|{sep})"
  return re.search(pattern, cmd, re.IGNORECASE) is not None


def _strip_identifier(cmd, name):
  pattern = rf"(?i)(?:(?<=^)|(?<=[^a-zA-Z0-9_-])){re.escape(name)}(?=$|[^a-zA-Z0-9_-])"
  return re.sub(pattern, " ", cmd)


def _has_prod_host_token(cmd):
  for match in HOST_VALUE_RE.finditer(cmd):
    if PROD_TOKEN_RE.search(match.group(1)):
      return True
  return False


def _evaluate_single_subcommand(subcmd, db_cfg):
  if not is_database_command(subcmd):
    return True, "OK"

  clean_cmd = URI_USERINFO_RE.sub(r"\1", subcmd)

  for host in db_cfg.get("prodHosts") or []:
    if host and _contains_identifier(clean_cmd, host):
      return False, f"Production database host detected: {host}"

  for db_name in db_cfg.get("prodDatabases") or []:
    if db_name and _contains_identifier(clean_cmd, db_name):
      return False, f"Production database name detected: {db_name}"

  if _has_prod_host_token(clean_cmd):
    return False, "Production database host detected"

  matched_test_dbs = [
      test_db
      for test_db in (db_cfg.get("testDatabases") or [])
      if test_db and _contains_identifier(clean_cmd, test_db)
  ]
  if matched_test_dbs:
    scrubbed = INLINE_QUERY_RE.sub(" ", clean_cmd)
    scrubbed = SQLITE_CMD_RE.sub(r"\1", scrubbed)
    for test_db in matched_test_dbs:
      scrubbed = _strip_identifier(scrubbed, test_db)
    prod_rem = PROD_TOKEN_RE.search(scrubbed)
    if prod_rem:
      return False, f"Production database name detected: {prod_rem.group(1)}"
    return True, f"Allowed configured test database: {matched_test_dbs[0]}"

  prod_match = PROD_TOKEN_RE.search(clean_cmd)
  if prod_match:
    return False, f"Production database name detected: {prod_match.group(1)}"

  return True, "OK"


def evaluate_database_safety(cmd, db_cfg=None):
  db_cfg = db_cfg or {}
  last_reason = "OK"
  for subcmd in _expand_shell_subcommands(cmd):
    is_safe, reason = _evaluate_single_subcommand(subcmd, db_cfg)
    if not is_safe:
      return False, reason
    if reason != "OK":
      last_reason = reason
  return True, last_reason


GIT_GLOBAL_VALUE_FLAGS = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
    "--exec-path",
    "--attr-source",
    "--list-cmds",
}

ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_KEYWORDS = {
    "(",
    "{",
    "}",
    ")",
    "!",
    "if",
    "then",
    "elif",
    "else",
    "do",
    "while",
    "until",
}


def _tokenize_subcommand(subcmd):
  """Splits a shell segment into token lists, failing closed on bad quotes."""
  try:
    toks = shlex.split(subcmd, posix=True)
    return [toks] if toks else []
  except ValueError:
    groups = []
    for chunk in re.split(r"(?:&&|\|\||[;|&\n])", subcmd):
      words = [t for t in re.split(r"[\s'\"]+", chunk.strip()) if t]
      if words:
        groups.append(words)
    return groups


def _unwrap_command_tokens(tokens):
  """Strips leading env assignments and shell wrappers, returning (exe, args)."""
  idx = 0
  n = len(tokens)
  while idx < n:
    tok = tokens[idx]
    if tok in SHELL_KEYWORDS:
      idx += 1
      continue
    if ENV_ASSIGN_RE.match(tok):
      idx += 1
      continue
    exe = os.path.basename(tok)
    if exe == "eval":
      j = idx + 1
      if j < n and tokens[j] == "--":
        j += 1
      if j < n:
        return "SHELL_C", [" ".join(tokens[j:])]
      return None, []
    if exe == "env":
      idx += 1
      while idx < n and tokens[idx].startswith("-"):
        flag = tokens[idx]
        if flag == "--":
          idx += 1
          break
        if flag in {"-S", "--split-string"}:
          if idx + 1 < n:
            return "SHELL_C", [tokens[idx + 1]]
          return None, []
        if flag.startswith("-S") and len(flag) > 2:
          return "SHELL_C", [flag[2:]]
        if flag.startswith("--split-string="):
          return "SHELL_C", [flag.split("=", 1)[1]]
        if flag in {"-u", "-C", "--unset", "--chdir"}:
          idx += 2
        else:
          idx += 1
      continue
    if exe in {"command", "builtin", "exec", "nohup", "time"}:
      idx += 1
      while idx < n and tokens[idx].startswith("-"):
        flag = tokens[idx]
        if flag == "--":
          idx += 1
          break
        idx += 1
      continue
    if exe == "sudo":
      idx += 1
      while idx < n and tokens[idx].startswith("-"):
        flag = tokens[idx]
        if flag == "--":
          idx += 1
          break
        if flag in {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U"}:
          idx += 2
        else:
          idx += 1
      continue
    if exe == "xargs":
      idx += 1
      while idx < n and tokens[idx].startswith("-"):
        flag = tokens[idx]
        if flag == "--":
          idx += 1
          break
        if flag in {"-n", "-L", "-s", "-P", "-I", "-d", "-E", "-a"}:
          idx += 2
        else:
          idx += 1
      continue
    if exe == "timeout":
      idx += 1
      while idx < n and tokens[idx].startswith("-"):
        flag = tokens[idx]
        if flag == "--":
          idx += 1
          break
        if flag in {"-k", "-s", "--kill-after", "--signal"}:
          idx += 2
        else:
          idx += 1
      if idx < n:
        idx += 1
      continue
    if exe == "nice":
      idx += 1
      while idx < n and tokens[idx].startswith("-"):
        flag = tokens[idx]
        if flag == "--":
          idx += 1
          break
        if flag in {"-n", "--adjustment"}:
          idx += 2
        else:
          idx += 1
      continue
    if exe in {"sh", "bash", "zsh", "dash", "ksh"}:
      j = idx + 1
      has_c = False
      while j < n:
        flag = tokens[j]
        if flag in {"--", "-"}:
          j += 1
          break
        if flag in {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}:
          j += 2
          continue
        if flag.startswith("--"):
          j += 1
          continue
        if flag.startswith(("-", "+")) and len(flag) > 1:
          is_minus = flag.startswith("-")
          for k, ch in enumerate(flag[1:], start=1):
            if is_minus and ch == "c":
              has_c = True
            elif ch in {"o", "O"}:
              if k == len(flag) - 1:
                j += 1
              break
          j += 1
          continue
        break
      if has_c and j < n:
        return "SHELL_C", [tokens[j]]
      return exe, tokens[idx + 1 :]
    return exe, tokens[idx + 1 :]
  return None, []


def _parse_git_subcommand(git_args):
  """Strips global git flags and returns (subcommand, subcommand_args, configs)."""
  idx = 0
  n = len(git_args)
  configs = []
  while idx < n:
    tok = git_args[idx]
    if tok == "--":
      if idx + 1 < n:
        return git_args[idx + 1], git_args[idx + 2 :], configs
      return None, [], configs
    if tok == "-c":
      if idx + 1 < n:
        configs.append(git_args[idx + 1])
      idx += 2
      continue
    if tok.startswith("-c") and len(tok) > 2:
      configs.append(tok[2:])
      idx += 1
      continue
    if tok in GIT_GLOBAL_VALUE_FLAGS:
      idx += 2
      continue
    if tok.startswith("-C") and len(tok) > 2:
      idx += 1
      continue
    if any(tok.startswith(flag + "=") for flag in GIT_GLOBAL_VALUE_FLAGS):
      idx += 1
      continue
    if tok.startswith("-"):
      idx += 1
      continue
    return tok, git_args[idx + 1 :], configs
  return None, [], configs


def _split_git_args(args, short_value_chars="", long_value_flags=()):
  """Splits subcommand args into (long_opts, short_opts, ordered, pos, has_dd)."""
  long_opts = []
  short_opts = set()
  ordered_opts = []
  positionals = []
  has_double_dash = False
  i = 0
  n = len(args)
  while i < n:
    tok = args[i]
    if tok == "--":
      has_double_dash = True
      positionals.extend(args[i + 1 :])
      break
    if tok.startswith("--"):
      long_opts.append(tok)
      ordered_opts.append(tok)
      if tok in long_value_flags:
        i += 2
      else:
        i += 1
      continue
    if tok.startswith("-") and len(tok) > 1:
      for j in range(1, len(tok)):
        ch = tok[j]
        short_opts.add(ch)
        ordered_opts.append(f"-{ch}")
        if ch in short_value_chars:
          if j == len(tok) - 1:
            i += 1
          break
      i += 1
      continue
    positionals.append(tok)
    i += 1
  return long_opts, short_opts, ordered_opts, positionals, has_double_dash


def _has_clean_force_config(configs):
  for item in configs:
    if "=" in item:
      key, val = item.split("=", 1)
      if key.strip().lower() == "clean.requireforce" and val.strip().lower() in {
          "false",
          "0",
          "no",
          "off",
      }:
        return True
  return False


def _has_push_force_config(configs):
  for item in configs:
    if "=" in item:
      key, val = item.split("=", 1)
      k_low = key.strip().lower()
      v_str = val.strip()
      if re.match(r"^remote\..+\.push$", k_low) and v_str.startswith("+"):
        return True
  return False


def _evaluate_git_tokens_safety(tokens):
  """Returns (safe: bool, reason: str) for MCP workflow_safety_check."""
  exe, rem = _unwrap_command_tokens(tokens)
  if not exe or exe == "SHELL_C":
    return True, "OK"

  if exe == "gh":
    _, _, _, gh_pos, _ = _split_git_args(
        rem,
        short_value_chars="R",
        long_value_flags=("--repo", "--hostname"),
    )
    if len(gh_pos) >= 2 and gh_pos[0] == "pr" and gh_pos[1] == "merge":
      return True, "Requires human operator approval at Target 75 Landing Gate"
    return True, "OK"

  if exe != "git":
    return True, "OK"

  subcmd, sub_args, git_configs = _parse_git_subcommand(rem)
  if not subcmd:
    return True, "OK"

  if subcmd == "reset":
    long_opts, _, _, _, _ = _split_git_args(sub_args)
    for bad_flag in ("--hard", "--merge", "--keep"):
      if bad_flag in long_opts:
        return False, f"Destructive Git command blocked: git reset {bad_flag}"
    return True, "OK"

  if subcmd == "clean":
    _, _, ordered_opts, _, _ = _split_git_args(
        sub_args,
        short_value_chars="e",
        long_value_flags=("--exclude",),
    )
    cfg_force = _has_clean_force_config(git_configs)
    dry_run = False
    force = cfg_force
    interactive = False
    for opt in ordered_opts:
      if opt in {"-n", "--dry-run"}:
        dry_run = True
      elif opt == "--no-dry-run":
        dry_run = False
      elif opt in {"-f", "--force"}:
        force = True
      elif opt == "--no-force":
        force = cfg_force
      elif opt in {"-i", "--interactive"}:
        interactive = True
      elif opt == "--no-interactive":
        interactive = False
    if (force or interactive) and not dry_run:
      return False, "Destructive Git command blocked: git clean"
    return True, "OK"

  if subcmd == "checkout":
    long_opts, short_opts, _, pos, has_dd = _split_git_args(
        sub_args,
        short_value_chars="bBU",
        long_value_flags=(
            "--orphan",
            "--conflict",
            "--inter-hunk-context",
            "--pathspec-from-file",
        ),
    )
    if (
        any(ch in short_opts for ch in ("f", "p", "2", "3"))
        or any(
            opt
            in {
                "--force",
                "--discard-changes",
                "--patch",
                "--ours",
                "--theirs",
                "--pathspec-from-file",
            }
            or opt.startswith("--pathspec-from-file=")
            for opt in long_opts
        )
    ):
      return False, "Destructive Git command blocked: git checkout --force"
    if has_dd:
      return False, "Destructive Git command blocked: git checkout -- <path>"
    if any(
        p in {".", ".."}
        or p.startswith(("./", "../"))
        or any(ch in p for ch in "*?[")
        for p in pos
    ):
      return False, "Destructive Git command blocked: path-based git checkout"
    creating_branch = (
        "b" in short_opts or "B" in short_opts or "--orphan" in long_opts
    )
    if not creating_branch and len(pos) >= 2:
      return False, "Destructive Git command blocked: git checkout <tree> <path>"
    return True, "OK"

  if subcmd == "switch":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args,
        short_value_chars="cC",
        long_value_flags=("--orphan", "--conflict"),
    )
    if (
        "f" in short_opts
        or "--force" in long_opts
        or "--discard-changes" in long_opts
    ):
      return False, "Destructive Git command blocked: git switch --discard-changes"
    return True, "OK"

  if subcmd == "restore":
    _, _, ordered_opts, _, _ = _split_git_args(
        sub_args,
        short_value_chars="s",
        long_value_flags=("--source", "--conflict", "--pathspec-from-file"),
    )
    staged = False
    worktree = False
    for opt in ordered_opts:
      if opt in {"-S", "--staged"}:
        staged = True
      elif opt == "--no-staged":
        staged = False
      elif opt in {"-W", "--worktree"}:
        worktree = True
      elif opt == "--no-worktree":
        worktree = False
    if not staged or worktree:
      return False, "Destructive Git command blocked: working-tree git restore"
    return True, "OK"

  if subcmd == "stash":
    _, _, _, pos, _ = _split_git_args(
        sub_args,
        short_value_chars="m",
        long_value_flags=("--message", "--pathspec-from-file"),
    )
    if pos and pos[0] in {"drop", "clear"}:
      return False, f"Destructive Git command blocked: git stash {pos[0]}"
    return True, "OK"

  if subcmd in {"filter-branch", "filter-repo"}:
    return False, f"Destructive Git command blocked: git {subcmd}"

  if subcmd == "reflog":
    _, _, _, pos, _ = _split_git_args(sub_args)
    if pos and pos[0] in {"delete", "expire"}:
      return False, f"Destructive Git command blocked: git reflog {pos[0]}"
    return True, "OK"

  if subcmd == "update-ref":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args, short_value_chars="m"
    )
    if "d" in short_opts or "--stdin" in long_opts:
      return False, "Destructive Git command blocked: git update-ref"
    return True, "OK"

  if subcmd == "push":
    long_opts, short_opts, _, pos, _ = _split_git_args(
        sub_args,
        short_value_chars="o",
        long_value_flags=("--repo", "--receive-pack", "--exec", "--push-option"),
    )
    has_force_flag = (
        "f" in short_opts
        or any(
            opt
            in {"--force", "--force-with-lease", "--force-if-includes", "--mirror"}
            or opt.startswith("--force-with-lease=")
            for opt in long_opts
        )
    )
    has_plus_refspec = (
        any(p.startswith("+") and len(p) > 1 for p in pos)
        or _has_push_force_config(git_configs)
    )
    if has_force_flag or has_plus_refspec:
      return False, "Destructive Git command blocked: force-push is forbidden"
    return True, "Requires human operator approval at Target 60 Push Gate"

  if subcmd == "rebase":
    return True, "Requires human operator approval before running git rebase"

  if subcmd == "branch":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args,
        short_value_chars="u",
        long_value_flags=("--set-upstream-to",),
    )
    has_force_short = any(ch in short_opts for ch in ("D", "M", "C", "f"))
    if has_force_short or "--force" in long_opts:
      return (
          True,
          "Requires human operator approval before running git branch force flags",
      )
    return True, "OK"

  if subcmd == "commit":
    if any(
        k in " ".join(tokens)
        for k in (
            "docs(history):",
            ".workflow/history",
            "history.json",
            "history.md",
        )
    ):
      return (
          True,
          "Requires human operator approval at Target 90 History Ledger Gate",
      )
    return True, "Requires human operator approval at Target 50 Commit Gate"

  return True, "OK"


def evaluate_git_safety(cmd):
  """Checks all shell subcommands for destructive Git operations."""
  last_reason = "OK"
  for subcmd in _expand_shell_subcommands(cmd):
    for tokens in _tokenize_subcommand(subcmd):
      is_safe, reason = _evaluate_git_tokens_safety(tokens)
      if not is_safe:
        return False, reason
      if reason != "OK" and last_reason == "OK":
        last_reason = reason
  return True, last_reason


def detect_hosting_mode(repo_dir="."):
  """Detects if repo_dir uses GitHub hosting with working gh CLI or pure Git.

  Returns:
      "github" if origin remote points to GitHub, gh CLI is available,
      gh auth status succeeds, and gh repo view succeeds.
      "pure_git" otherwise.
  """
  try:
    res = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if res.returncode != 0:
      return "pure_git"
    remote_url = (res.stdout or "").strip()
    if not remote_url:
      return "pure_git"
    is_github = "github.com" in remote_url.lower()
    if not is_github:
      return "pure_git"

    if not shutil.which("gh"):
      return "pure_git"

    auth_res = subprocess.run(
        ["gh", "auth", "status"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if auth_res.returncode != 0:
      return "pure_git"

    repo_res = subprocess.run(
        ["gh", "repo", "view"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if repo_res.returncode != 0:
      return "pure_git"

    return "github"
  except (subprocess.SubprocessError, OSError):
    return "pure_git"


def poll_ci_status(
    pr_number,
    ws,
    quiescence_sec=None,
    poll_interval_sec=None,
    max_wait_sec=600,
    hosting_mode=None,
):
  cfg = resolve_workflow_config(ws)
  ci_cfg = cfg.get("ci", {})
  q_sec = (
      quiescence_sec
      if quiescence_sec is not None
      else ci_cfg.get("quiescenceSeconds", 180)
  )
  p_sec = (
      poll_interval_sec
      if poll_interval_sec is not None
      else ci_cfg.get("pollIntervalSeconds", 30)
  )

  effective_mode = (
      hosting_mode if hosting_mode is not None else detect_hosting_mode(ws)
  )
  if effective_mode == "pure_git" or pr_number <= 0:
    return {
        "mode": "pure_git",
        "ciStatus": "SKIPPED",
        "quiescenceSatisfied": True,
        "readyToLand": True,
        "unresolvedBotCommentsCount": 0,
        "botComments": [],
        "message": (
            "Pure Git Mode: Repository is not GitHub-hosted, GitHub CLI is not"
            " available, or PR number is 0. Run local tests (make all) for"
            " verification."
        ),
    }

  start_time = time.time()
  ci_completed_at = None

  while True:
    now = time.time()
    elapsed_total = now - start_time
    if elapsed_total > max_wait_sec:
      return {
          "mode": "github",
          "ciStatus": "TIMEOUT",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": (
              f"Timed out after {int(elapsed_total)}s waiting for CI or"
              " quiescence."
          ),
      }

    try:
      cmd = [
          "gh",
          "pr",
          "view",
          str(pr_number),
          "--json",
          "statusCheckRollup,comments,reviews,state,mergeable,mergeStateStatus",
      ]
      res = subprocess.run(
          cmd, cwd=ws, capture_output=True, text=True, check=True
      )
      data = json.loads(res.stdout)
    except subprocess.CalledProcessError as exc:
      stderr = (exc.stderr or "").strip()
      return {
          "mode": "github_error",
          "ciStatus": "ERROR",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": f"gh pr view failed: {stderr}",
      }
    except Exception as exc:
      return {
          "mode": "github_error",
          "ciStatus": "ERROR",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": f"Error parsing gh output: {exc}",
      }

    checks = data.get("statusCheckRollup") or []
    all_completed = True
    has_failure = False
    failed_checks = []
    max_check_completed_at = None

    for c in checks:
      st = c.get("status", "").upper()
      con = (c.get("conclusion") or "").upper()
      name = c.get("name") or c.get("context") or "check"
      if st != "COMPLETED":
        all_completed = False
      elif con in (
          "FAILURE",
          "CANCELLED",
          "TIMED_OUT",
          "STARTUP_FAILURE",
          "ACTION_REQUIRED",
      ):
        has_failure = True
        failed_checks.append(name)

      completed_str = (
          c.get("completedAt") or c.get("updatedAt") or c.get("createdAt")
      )
      if completed_str:
        try:
          dt = datetime.datetime.fromisoformat(
              completed_str.replace("Z", "+00:00")
          )
          ts = dt.timestamp()
          if max_check_completed_at is None or ts > max_check_completed_at:
            max_check_completed_at = ts
        except Exception:
          pass

    if has_failure:
      return {
          "mode": "github",
          "ciStatus": "FAILURE",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "failedChecks": failed_checks,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": f"CI checks failed: {failed_checks}",
      }

    raw_comments = data.get("comments") or []
    raw_reviews = data.get("reviews") or []
    bot_comments = []

    for cm in raw_comments:
      author = (cm.get("author") or {}).get("login", "")
      if "[bot]" in author.lower() or author.lower() in (
          "github-actions",
          "coderabbitai",
          "codecov",
          "sonarcloud",
          "gemini-code-assist",
          "copilot",
      ):
        bot_comments.append({
            "type": "comment",
            "author": author,
            "body": cm.get("body", ""),
            "createdAt": cm.get("createdAt", ""),
        })

    for rv in raw_reviews:
      author = (rv.get("author") or {}).get("login", "")
      if "[bot]" in author.lower() or author.lower() in (
          "github-actions",
          "coderabbitai",
          "codecov",
          "sonarcloud",
          "gemini-code-assist",
          "copilot",
      ):
        state = rv.get("state", "").upper()
        if state not in ("APPROVED", "DISMISSED"):
          bot_comments.append({
              "type": "review",
              "author": author,
              "body": rv.get("body", ""),
              "state": state,
              "submittedAt": rv.get("submittedAt", ""),
          })

    has_checks = len(checks) > 0 or elapsed_total >= 15
    if all_completed and has_checks:
      if ci_completed_at is None:
        if max_check_completed_at is not None:
          ci_completed_at = min(now, max_check_completed_at)
        else:
          ci_completed_at = now

      latest_activity = ci_completed_at
      for bc in bot_comments:
        ts_str = bc.get("createdAt") or bc.get("submittedAt")
        if ts_str:
          try:
            dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            ts = dt.timestamp()
            if ts > latest_activity:
              latest_activity = min(now, ts)
          except Exception:
            pass

      elapsed_since_activity = now - latest_activity
      if elapsed_since_activity >= q_sec or max_wait_sec <= 1:
        return {
            "mode": "github",
            "ciStatus": "SUCCESS",
            "quiescenceSatisfied": True,
            "readyToLand": len(bot_comments) == 0,
            "unresolvedBotCommentsCount": len(bot_comments),
            "botComments": bot_comments,
            "mergeable": data.get("mergeable"),
            "mergeStateStatus": data.get("mergeStateStatus"),
            "message": (
                "CI completed successfully and"
                f" {int(elapsed_since_activity)}s quiescence satisfied."
            ),
        }

    if max_wait_sec <= 1:
      return {
          "mode": "github",
          "ciStatus": "PENDING" if not all_completed else "SUCCESS",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "unresolvedBotCommentsCount": len(bot_comments),
          "botComments": bot_comments,
          "message": "CI in progress or awaiting quiescence window.",
      }

    time.sleep(p_sec)


def handle_initialize(request_id):
  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "result": {
          "protocolVersion": "2024-11-05",
          "capabilities": {"tools": {}},
          "serverInfo": {
              "name": "makeflow-guard",
              "version": "1.4.1",
          },
      },
  }


def handle_tools_list(request_id):
  tools = [
      {
          "name": "workflow_get_state",
          "description": (
              "Reads Memento state files in the workspace to find ticket ID,"
              " branch name, active target, and completed milestones."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "workspaceDir": {
                      "type": "string",
                      "description": "Absolute path to the project repository root",
                  },
                  "stateDir": {
                      "type": "string",
                      "description": (
                          "Path to the state directory (defaults to"
                          " .workflow/state inside workspaceDir)"
                      ),
                  },
              },
          },
      },
      {
          "name": "workflow_advance_target",
          "description": (
              "Checks whether prerequisites pass to move from currentTarget to"
              " nextTarget in the Makefile DAG."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "currentTarget": {
                      "type": "string",
                      "description": (
                          "Current target ID, for example T20, T30, T40, T50, T75"
                      ),
                  },
                  "nextTarget": {
                      "type": "string",
                      "description": (
                          "Next target ID, for example T30, T45, T60, T80"
                      ),
                  },
                  "evidence": {
                      "type": "object",
                      "description": (
                          "Proof of prerequisite completion, such as"
                          " humanPlanApproved or blockersCount"
                      ),
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  },
                  "stateDir": {
                      "type": "string",
                      "description": (
                          "Path to the state directory (defaults to"
                          " .workflow/state inside workspaceDir)"
                      ),
                  },
              },
              "required": ["currentTarget", "nextTarget"],
          },
      },
      {
          "name": "workflow_verify_tree",
          "description": (
              "Computes the Git Merkle tree hash with 'git write-tree' in"
              " workspaceDir and compares it to expectedApprovedTree."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "expectedApprovedTree": {
                      "type": "string",
                      "description": "Expected 40-character Git tree hash",
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Absolute path to the Git repository root",
                  },
              },
              "required": ["expectedApprovedTree"],
          },
      },
      {
          "name": "workflow_safety_check",
          "description": (
              "Checks a shell command or connection string to make sure that"
              " it does not connect to a production database or run a"
              " destructive Git command."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "commandLine": {
                      "type": "string",
                      "description": "Shell command line to inspect",
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  },
              },
              "required": ["commandLine"],
          },
      },
      {
          "name": "workflow_get_config",
          "description": (
              "Reads active workflow configuration and model assignments from"
              " workspaceDir, user configuration, or environment variables."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  }
              },
          },
      },
      {
          "name": "workflow_poll_ci",
          "description": (
              "Polls GitHub pull request checks and review comments until CI"
              " completes and the review bot quiescence window passes."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "prNumber": {
                      "type": "integer",
                      "description": "Pull request number to monitor",
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  },
                  "quiescenceSeconds": {
                      "type": "integer",
                      "description": "Seconds of silence with no new bot activity required (overrides config)",
                  },
                  "pollIntervalSeconds": {
                      "type": "integer",
                      "description": "Seconds between status polls (overrides config)",
                  },
                  "maxWaitSeconds": {
                      "type": "integer",
                      "description": "Maximum seconds before polling timeout (default 600)",
                  },
              },
              "required": ["prNumber"],
          },
      },
      {
          "name": "workflow_query_scars",
          "description": (
              "Queries public synthetic scars database using full-text search"
              " (FTS5) or keywords with category and language filters."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "query": {
                      "type": "string",
                      "description": "Keyword search query for scars (e.g. 'BaseScript', 'isolation')",
                  },
                  "category": {
                      "type": "string",
                      "description": "Optional category or slug filter (e.g. 'Testing', 'Architecture')",
                  },
                  "language": {
                      "type": "string",
                      "description": "Optional programming language filter (e.g. 'Python', 'Go')",
                  },
                  "limit": {
                      "type": "integer",
                      "description": "Maximum number of scars to return (default 3)",
                  },
                  "source": {
                      "type": "string",
                      "description": "Optional source filter (e.g. 'makeflow', 'upstream')",
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  },
                  "dbPath": {
                      "type": "string",
                      "description": "Optional explicit SQLite scars database path",
                  },
              },
              "required": ["query"],
          },
      },
  ]
  return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}


def _error_result(request_id, message):
  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "result": {
          "isError": True,
          "content": [{"type": "text", "text": message}],
      },
  }


def is_ticket_active(state_dir):
  """Checks whether an active in-progress ticket exists in state_dir."""
  if not state_dir or not os.path.isdir(state_dir):
    return False
  state_files = [f for f in os.listdir(state_dir) if f.endswith(".md")]
  if not state_files:
    return False

  next_action_file = os.path.join(state_dir, "99_next_action.md")
  if os.path.isfile(next_action_file):
    try:
      with open(next_action_file, "r", encoding="utf-8") as f:
        content = f.read()
      if (
          "# Target 90 Finished: COMPLETE" in content
          or "Status: COMPLETE" in content
          or "make finish satisfied" in content
      ):
        return False
    except Exception:
      pass

  return True


def handle_tools_call(request_id, tool_name, arguments):
  arguments = arguments or {}

  if tool_name == "workflow_get_state":
    ws = resolve_workspace_dir(arguments)
    if ws is None:
      return _error_result(
          request_id,
          "BLOCKED: Running inside plugin installation directory without a workspaceDir. "
          "Pass 'workspaceDir' with the absolute path to your project workspace.",
      )
    raw_state_dir = arguments.get("stateDir", ".workflow/state")
    state_dir = (
        raw_state_dir
        if os.path.isabs(raw_state_dir)
        else os.path.join(ws, raw_state_dir)
    )
    if not os.path.isdir(state_dir):
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": (
                      f"State directory '{state_dir}' does not exist."
                      " Ready for Target 10 intake."
                  ),
              }]
          },
      }

    files = sorted(f for f in os.listdir(state_dir) if f.endswith(".md"))
    if not files:
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": (
                      f"State directory '{state_dir}' is clean (0 state cards)."
                      " Ready for Target 10 intake."
                  ),
              }]
          },
      }

    if not is_ticket_active(state_dir):
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": (
                      f"Previous ticket is complete in '{state_dir}'."
                      " Ready for Target 10 intake."
                  ),
              }]
          },
      }

    summary = f"Found {len(files)} state card(s) in {state_dir}:\n" + "\n".join(
        f"- {f}" for f in files
    )
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": summary}]},
    }

  elif tool_name == "workflow_advance_target":
    current = arguments.get("currentTarget", "").strip()
    next_t = arguments.get("nextTarget", "").strip()
    evidence = arguments.get("evidence") or {}

    if current not in VALID_TRANSITIONS:
      return _error_result(
          request_id,
          f"BLOCKED: Unknown currentTarget '{current}'. Use T00 through T90.",
      )
    if next_t not in ALL_TARGETS:
      return _error_result(
          request_id,
          f"BLOCKED: Unknown nextTarget '{next_t}'. Use T00 through T90.",
      )
    if next_t not in VALID_TRANSITIONS[current]:
      allowed = sorted(VALID_TRANSITIONS[current])
      return _error_result(
          request_id,
          f"BLOCKED: Transition {current} -> {next_t} is not valid in the"
          f" Makefile DAG. Allowed next targets from {current}: {allowed}.",
      )

    gate_key = GATE_EVIDENCE_KEYS.get((current, next_t))
    if gate_key and not evidence.get(gate_key):
      return _error_result(
          request_id,
          f"BLOCKED: Cannot move {current} -> {next_t} without"
          f" {gate_key} = true.",
      )

    if current == "T40" and next_t == "T45":
      blockers = evidence.get("blockersCount", 1)
      if blockers > 0:
        return _error_result(
            request_id,
            f"BLOCKED: Critic reported {blockers} blocker(s). Route to T42"
            " (triaged-blockers) before T45.",
        )

    if current == "T40" and next_t == "T42":
      blockers = evidence.get("blockersCount", 1)
      if blockers == 0:
        return _error_result(
            request_id,
            "BLOCKED: Critic reported 0 blockers. Route to T45"
            " (approved-review) instead of T42.",
        )

    if arguments.get("workspaceDir") or arguments.get("stateDir"):
      ws = resolve_workspace_dir(arguments)
      if ws is None:
        return _error_result(
            request_id,
            "BLOCKED: Missing workspaceDir when running in plugin directory.",
        )
      raw_state_dir = arguments.get("stateDir", ".workflow/state")
      state_dir = (
          raw_state_dir
          if os.path.isabs(raw_state_dir)
          else os.path.join(ws, raw_state_dir)
      )
      if os.path.isdir(state_dir):
        req_card = REQUIRED_STATE_CARDS.get((current, next_t))
        if req_card and not os.path.isfile(os.path.join(state_dir, req_card)):
          return _error_result(
              request_id,
              f"BLOCKED: Required state card '{req_card}' does not exist in"
              f" '{state_dir}' for transition {current} -> {next_t}.",
          )

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": f"SUCCESS: Target transition {current} -> {next_t} passed.",
            }]
        },
    }

  elif tool_name == "workflow_verify_tree":
    expected = arguments.get("expectedApprovedTree", "").strip()
    ws = resolve_workspace_dir(arguments)
    if ws is None:
      return _error_result(
          request_id,
          "BLOCKED: Running inside plugin installation directory without a workspaceDir. "
          "Pass 'workspaceDir' with the path to the Git repository.",
      )
    try:
      res = subprocess.run(
          ["git", "write-tree"],
          cwd=ws,
          capture_output=True,
          text=True,
          check=True,
      )
      actual = res.stdout.strip()
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": json.dumps({
                      "matched": actual == expected,
                      "actualTree": actual,
                      "expectedTree": expected,
                      "workspaceDir": ws,
                  }),
              }]
          },
      }
    except subprocess.CalledProcessError as exc:
      stderr = (exc.stderr or "").strip()
      return _error_result(
          request_id,
          f"git write-tree failed in '{ws}': {stderr}. Pass workspaceDir with"
          " the path to the Git repository.",
      )
    except Exception as exc:
      return _error_result(request_id, f"git write-tree error in '{ws}': {exc}")

  elif tool_name == "workflow_safety_check":
    cmd = arguments.get("commandLine", "")
    ws = resolve_workspace_dir(arguments)
    cfg = resolve_workflow_config(ws)
    is_safe, reason = evaluate_database_safety(cmd, cfg.get("database"))
    if is_safe:
      git_safe, git_reason = evaluate_git_safety(cmd)
      if not git_safe:
        is_safe, reason = False, git_reason
      elif reason == "OK" and git_reason != "OK":
        reason = git_reason
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"safe": is_safe, "reason": reason}),
                }
            ]
        },
    }

  elif tool_name == "workflow_get_config":
    ws = resolve_workspace_dir(arguments)
    cfg = resolve_workflow_config(ws)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps(cfg, indent=2),
            }]
        },
    }

  elif tool_name == "workflow_poll_ci":
    pr_num = arguments.get("prNumber", 0)
    ws = resolve_workspace_dir(arguments)
    if ws is None:
      return _error_result(
          request_id,
          "BLOCKED: Running inside plugin installation directory without a workspaceDir. "
          "Pass 'workspaceDir' with the path to the Git repository.",
      )
    q_sec = arguments.get("quiescenceSeconds")
    p_sec = arguments.get("pollIntervalSeconds")
    max_wait = arguments.get("maxWaitSeconds", 600)
    res = poll_ci_status(
        pr_number=pr_num,
        ws=ws,
        quiescence_sec=q_sec,
        poll_interval_sec=p_sec,
        max_wait_sec=max_wait,
    )
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps(res, indent=2),
            }]
        },
    }

  elif tool_name == "workflow_query_scars":
    query = (arguments.get("query") or "").strip()
    if not query:
      return _error_result(
          request_id,
          "Missing required argument 'query'. Provide a keyword or search query.",
      )

    ws = resolve_workspace_dir(arguments)
    category = arguments.get("category")
    language = arguments.get("language")
    source = arguments.get("source")
    try:
      limit = int(arguments.get("limit", 3))
    except (ValueError, TypeError):
      limit = 3

    if resolve_scars_db_path is None or query_scars is None:
      return _error_result(
          request_id,
          "Scars query engine is unavailable. Ensure scripts/sync_synthetic_scars.py is present.",
      )

    explicit_db = arguments.get("dbPath") or arguments.get("db_path")
    db_path = resolve_scars_db_path(
        db_path=explicit_db,
        workspace_dir=ws,
        package_root=_REPO_ROOT,
    )

    if not os.path.isfile(db_path):
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": json.dumps({
                      "status": "UNINITIALIZED",
                      "message": (
                          f"Scars database not found at '{db_path}'. "
                          "Bundled registry database is missing from skill package. "
                          "Reinstall makeflow plugin or run 'make sync-scars' (maintainer) to generate."
                      ),
                      "scars": [],
                  }, indent=2),
              }]
          },
      }

    results = query_scars(
        db_path=db_path,
        query=query,
        category=category,
        language=language,
        limit=limit,
        source=source,
    )

    if not results:
      is_empty = False
      conn = None
      try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM scars")
        row = cur.fetchone()
        if row and row[0] == 0:
          is_empty = True
      except Exception:
        is_empty = True
      finally:
        if conn:
          conn.close()

      if is_empty:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "EMPTY",
                        "message": (
                            f"Scars database at '{db_path}' contains 0 scars. "
                            "Bundled registry database is unpopulated. "
                            "Run 'make sync-scars' (maintainer) to rebuild."
                        ),
                        "scars": [],
                    }, indent=2),
                }]
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "status": "OK",
                    "totalResults": len(results),
                    "scars": results,
                }, indent=2),
            }]
        },
    }

  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "error": {"code": -32601, "message": f"Method {tool_name} not found"},
  }


def main():
  for line in sys.stdin:
    if not line.strip():
      continue
    try:
      req = json.loads(line)
      req_id = req.get("id")
      method = req.get("method")

      if method == "initialize":
        resp = handle_initialize(req_id)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
      elif method == "notifications/initialized":
        pass
      elif method == "tools/list":
        resp = handle_tools_list(req_id)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
      elif method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        resp = handle_tools_call(req_id, name, args)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
      else:
        if req_id is not None:
          resp = {
              "jsonrpc": "2.0",
              "id": req_id,
              "error": {"code": -32601, "message": f"Unknown method: {method}"},
          }
          sys.stdout.write(json.dumps(resp) + "\n")
          sys.stdout.flush()
    except Exception as exc:
      err_resp = {
          "jsonrpc": "2.0",
          "id": None,
          "error": {"code": -32700, "message": f"Parse error: {exc}"},
      }
      sys.stdout.write(json.dumps(err_resp) + "\n")
      sys.stdout.flush()


if __name__ == "__main__":
  main()

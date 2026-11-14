# Polyglot Stack Guide: Python

This guide covers detection markers, test and lint commands, smoke test templates, and database isolation for Python.

## 1. Detection Markers

Look for these files when scanning a Python subproject:

- `pyproject.toml`, `requirements.txt`, `setup.py`, `Pipfile`, `tox.ini`

## 2. Standard Commands

Run these commands inside the Python subproject directory:

- Targeted Test: `pytest <subproject>/tests/ -v` (or `python3 -m unittest discover <subproject>/tests`)
- Format: `ruff format --check .` or `black --check .`
- Lint: `ruff check .` or `flake8 .`

## 3. Smoke Test Template

If the subproject has no test directory, create `tests/test_smoke.py`:

```python
"""Smoke test template for Python subprojects."""


def test_smoke():
  """Makes sure that the test runner executes cleanly."""
  assert True
```

## 4. Test Database and Network Isolation

Follow four isolation rules in Python tests:

1. Use SQLite in-memory connections (`sqlite:///:memory:`) for unit tests.
2. For PostgreSQL or MySQL integration tests, set `TEST_DATABASE_URL=postgres://user:pass@localhost:5432/<app>_test`.
3. Never point `DATABASE_URL` to staging or production hosts.
4. Mock external HTTP requests with `pytest-mock` or `unittest.mock`.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place reusable modules in `src/<pkg>/` or `lib/` with `__init__.py` and zero executable shebang headers (`SCAR-ARCH-05`).
- **Executable Entrypoints and Controllers**: Place thin CLI wrappers in `scripts/` subclassing `BaseScript` and delegating `run()` to `lib/` without cross-script imports.
- **Test Suite Placement and Naming**: Place unit and integration suites in `tests/test_*.py` so `pytest` and `unittest` discover them automatically.
- **Structural Anti-Patterns to Flag at Target 15**: Flag peer script imports (`from scripts.other import ...`), shebangs inside `lib/`, or unguarded module-level side effects.

# Auris Studio project instructions

## Python environment

- Use `reader/.venv` as the project's Python environment.
- Run Python commands from the repository root with `reader/.venv/bin/python`,
  or from `reader` with `.venv/bin/python`. On Windows the same environment
  lives at `reader\.venv\Scripts\python.exe`.
- Do not use the system `python` command for project tests, scripts, or
  dependency checks.
- Run the full test suite from `reader` with:
  `.venv/bin/python -m unittest discover -s tests -p "test_*.py"`

## Branching

- `main` is the released state, `dev` is the always-working merged state.
- Every topic gets its own `feature/<topic>` branch and merges back into `dev`
  once its tests pass.

## Conventions

- Environment variables use the `AURIS_STUDIO_` prefix.
- Hungarian text handling lives in `reader/core/hungarian_numbers.py` and
  `reader/core/parser/`; keep Hungarian-specific rules out of the engine code.

# Contributing

Thanks for considering a contribution.

## Development setup

```bash
git clone <your-fork-url>
cd edge-orchestrator
pip install -e ".[dev]"   # installs pytest; the package itself has no runtime deps
```

## Running tests

```bash
python3 -m unittest discover -s tests -v
# or
pytest
```

## Regenerating sample data

```bash
python3 scripts/generate_sample_data.py
```

This is deterministic (fixed random seed) so diffs stay meaningful in
pull requests.

## Style

- Standard library only for runtime code under `src/edge_orchestrator/`.
  If a change genuinely needs a third-party package, open an issue first
  to discuss — the zero-dependency design is a deliberate project goal,
  not an oversight.
- Type hints on public functions/methods.
- Docstrings on every module explaining *why* it exists, not just what it
  does — this project favors explaining the edge-computing problem being
  solved over restating the code.
- New services go in `src/edge_orchestrator/services/`, are wired into
  `container.py`, and get a corresponding `tests/test_<service>.py`.

## Submitting changes

1. Fork and branch from `main`.
2. Add tests for new behavior.
3. Make sure `python3 -m unittest discover -s tests` passes.
4. Open a pull request describing the change and, if relevant, which part
   of `docs/ARCHITECTURE.md` it affects.

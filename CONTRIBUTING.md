# Contributing

Contributions are welcome when they improve reproducibility, safety, or measured coding outcomes.

## Good contributions

- a minimal reproducible Harness failure;
- a deterministic regression test for an existing failure;
- an adaptive routing or budget policy with an ablation;
- support for a new local model/server contract;
- a new verification adapter with protected tests;
- documentation that makes setup or benchmark claims more precise.

## Development setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[test]'
cp config.example.json config.json
.venv/bin/python -m pytest tests -q
```

Most unit tests mock model calls. `tests/live_gateway.py` makes real requests to the configured local model and is intentionally not part of the default test suite.

## Pull requests

1. Keep changes scoped and explain the failure or capability being addressed.
2. Add deterministic tests for behavior changes.
3. Report checks actually run; do not claim hidden-test or model improvements without evidence.
4. Do not include model weights, task datasets without redistribution rights, credentials, local paths, or `.state` logs.
5. For benchmark changes, disclose model, quantization, serving stack, prompt, task set, budgets, concurrency, retries, and human intervention.
6. Preserve the safety properties around loopback binding, scoped writes, protected paths, frozen candidates, and guarded apply.

## Prompt and role changes

Prompt changes are code changes. A proposed role prompt should include:

- the failure mode it addresses;
- expected mechanism;
- an evaluation set not created from the same examples used in the prompt;
- accuracy, token, and time deltas;
- regressions and non-convergent runs.

## Reporting results

Use “public-test pass”, “independent-oracle pass”, and “official hidden-test pass” as separate terms. Multi-call Agent outcomes must not be labeled pass@1.

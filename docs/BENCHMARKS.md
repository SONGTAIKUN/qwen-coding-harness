# Initial benchmark methodology

## Purpose

The initial evaluation asks a narrow question:

> Can a persisted, test-driven feedback loop recover tasks that the same local model failed in one-shot generation?

It does not establish parity with any named commercial model and does not measure end-to-end performance on a large software repository.

## Setup

| Field | Value |
| --- | --- |
| Task count | 100 |
| Baseline date | 2026-09-17 |
| Serving | oMLX 0.6.4 on Apple Silicon |
| Base model class | approximately 100B open-weight MoE, locally served |
| Baseline mode | one-shot code generation |
| Baseline output ceiling | 16,384 tokens |
| Agent mode | persisted multi-role workflow with deterministic public checks |
| Agent concurrency | maximum four shared model requests |
| Per-job ceiling | 196,608 completion tokens and two active hours |
| Repair ceiling | three rounds |

The 49 baseline failures were subsequently attempted with the Agent workflow. Tasks were run serially in the later controlled batches while research subagents could run concurrently within one task.

## Results

| Category | Tasks |
| --- | ---: |
| Baseline one-shot public pass | 51 |
| Agent-recovered public pass with no known counterexample | 41 |
| Total with no known counterexample | 92 |
| Additional public passes later rejected by independent oracle | 4 |
| Agent attempted but not passed | 4 |
| Total public-test pass before oracle rejection | 96 |

Independent oracle coverage among the 41 recovered candidates:

- 19 passed a problem-specific exhaustive, randomized, state-space, or semantic oracle;
- 22 currently have public checks but no independent oracle result;
- 4 additional public-test-passing candidates were rejected by an oracle and are not included in the 41.

The phrase **no known counterexample** is intentional. It is weaker than hidden-test correctness.

## What changed

The Agent did not increase the single-response output limit. It distributed work across role calls and persisted research, plans, code, tests, diffs, and failures outside any individual model response. It could therefore continue after a single 16,384-token turn and use executable feedback.

One recorded example, problem `3423`, followed this path:

1. the baseline answer stopped at the output limit and was wrong;
2. the first Agent implementation produced `18` where the public case expected `21`;
3. the Harness entered the Debugger stage;
4. the repaired candidate passed 2/2 public cases;
5. after freezing, a model-free oracle passed 2,000 randomized update sequences.

## Why this is not a leaderboard score

- The checks were bundled public examples, not the official hidden evaluator.
- Some tasks required custom input/output adapters and one required a semantic special judge.
- Agent runs used substantially more calls, tokens, and wall time than one-shot generation.
- The evaluation targeted known one-shot failures after the baseline; it is not pass@1.
- Not every surviving candidate has an independent oracle.
- Algorithmic tasks have much cleaner specifications and verification than day-to-day repository work.
- The project does not publish copied task statements, raw generations, or third-party evaluation data in this repository.

## Reproducibility artifacts

This public repository includes:

- aggregate data: `docs/benchmarks/summary.json`;
- generated visuals: `docs/assets/`;
- the renderer: `scripts/render_benchmark_assets.py`;
- the generic public/hidden evaluation adapter in `src/qwen_harness/evaluation.py`;
- Harness unit tests and an example project.

Raw local runs contain model logs and third-party task text and are intentionally excluded by `.gitignore`.

## Next evaluation

The next credible milestone is a frozen set of real repository issues split across bug fixes, features, refactors, tests, and integration work. Comparisons should hold the following constant:

- model and quantization;
- repository revision;
- task prompt and acceptance tests;
- inference and repair budget;
- environment and dependency cache;
- human intervention policy.

Report task success, regressions, human corrections, wall time, model calls, prompt/completion tokens, and failure phase. Compare at least one-shot, direct OpenCode, Harness, and a stronger reference model under disclosed budgets.

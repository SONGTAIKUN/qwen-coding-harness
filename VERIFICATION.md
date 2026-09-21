# Verification, 2026-09-20

- OpenCode: official macOS arm64 v1.18.31; release archive SHA-256 matched.
- Python dependency check: `pip check` passed.
- Automated tests: 25 passed; two third-party deprecation warnings only.
- The local test report is stored under ignored `.state/` and is not published.
- Actual OpenCode -> MCP -> Qwen -> file tools -> macOS sandbox tests -> independent review -> apply: passed.
- Live workflow: `20260920-095100-3650a844`, status `applied`.
- Source changed: `examples/smoke/calculator.py`, subtraction corrected to addition.
- Protected tests unchanged; all three unittest cases passed.
- Harness workflow: 15 model calls, 3,213 completion tokens, 23,183 prompt tokens, 111.05 seconds. These numbers exclude OpenCode orchestration/title requests.
- Five simultaneous real requests: four active, one queued; all completed, no remaining active/waiting requests.
- Live concurrency report: `.state/live-concurrency-test.json`.
- OpenCode `none` variant confirmed at gateway as `enable_thinking: false`.
- OpenCode project working directory and project-bound MCP connection checked.
- Public and hidden evaluation execution tested with sandboxed stdin/stdout programs; no full LiveCodeBench dataset run.

Limits: This verifies a small Python project and short concurrent requests, not four simultaneous 64K contexts, large repository performance, or improved benchmark accuracy. JavaScript/npm project verification remains dependent on repairing the pre-existing Node dynamic-library issue. The macOS runner is not VM-grade containment and has no hard per-process memory cap.

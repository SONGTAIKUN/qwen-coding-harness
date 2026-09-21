# Roadmap

Qwen Coding Harness is intended as a long-term empirical project. Priorities are ordered by expected impact on real software tasks, not by interface novelty.

## v0.1: Verifiable local loop

- [x] OpenCode MCP integration
- [x] shared four-request model gate
- [x] isolated project snapshots and scoped writes
- [x] parallel research and non-overlapping implementation
- [x] deterministic verification
- [x] Debugger repair loop
- [x] independent-context Reviewer
- [x] checkpoint, resume, diff, guarded apply, and backups
- [x] public/hidden evaluation separation
- [x] initial 100-task study with disclosed limitations

## v0.2: Adaptive workflow

- [ ] classify bug, feature, refactor, migration, performance, and security tasks
- [ ] select only relevant research roles instead of requiring all roles
- [ ] permit planning from partial research when one role fails to converge
- [ ] reserve budgets by phase so research cannot starve implementation or repair
- [ ] add early stopping and repeated-truncation detection
- [ ] expose per-role budget profiles in project configuration

## v0.3: Stronger verification

- [ ] create a separate Test Author workspace
- [ ] review and freeze generated regression tests before implementation
- [ ] add property, metamorphic, fuzz, and mutation checks
- [ ] support semantic, floating-point, and output-special-judge adapters
- [ ] track which acceptance criteria each check covers
- [ ] distinguish flaky infrastructure failures from candidate failures

## v0.4: Repository intelligence

- [ ] persistent symbol and reference index
- [ ] test-to-code and dependency mapping
- [ ] controlled Git log/blame/history tools
- [ ] repository-specific conventions and architecture memory
- [ ] incremental snapshots for repositories larger than 250 MB
- [ ] safe file rename and deletion plans

## v0.5: Engineering tools

- [ ] allowlisted build and package-manager tools
- [ ] browser and screenshot verification
- [ ] database migration sandboxes
- [ ] container-backed Linux runner
- [ ] language adapters beyond Python
- [ ] structured performance and security profilers

## Evaluation program

- [ ] publish a frozen real-repository issue suite
- [ ] compare one-shot, direct agent, Harness, and stronger reference models
- [ ] report accuracy, regressions, human corrections, time, and tokens
- [ ] add ablations for each role and feedback mechanism
- [ ] require benchmark claims to include raw aggregate data and failure analysis

The target is not to maximize a single public score. The target is to find which combinations of model, tools, context, tests, roles, and budget reliably close capability gaps on defined task distributions.

# 0.1.0 (2026-10-14)

First public version.

- Four checks: `anchors`, `known-bad`, `mutants`, `killers`; `all` runs them in order and stops at the first red. A mutant is killed only when ALL its expected tests turn red; a mutant that turns every test red is a broken suite, not evidence.
- Result readers: regex over test output (named groups `id`/`status`) and JUnit XML.
- Config: `new_tests`, `must_fail_on_baseline`, `allow_green_on_both`, anchored `mutants[]` with `new_code_only`.
- Markdown and `--json` reports (states pass/fail/absent); exit codes 0 green, 1 red, 2 config/usage/unreadable; empty or duplicated results and stale JUnit files are errors.
- Path safety: mutant files must be relative, inside the tree, regular files; `--work` outside the candidate; process-group kill on timeout.
- Synthetic example (`example/`) and 17 end-to-end tests that show each red path.

# Contributing

- Keep it one file and zero dependencies. If a feature needs a dependency, open an issue first.
- Every behaviour change needs a test in `tests/` that is **red before** the change and green after
  (this repository runs its own gate on itself in CI, so expect that to be checked).
- Run locally: `python3 -m unittest -v` from the repository root, then `example/try.sh`.
- Report bugs with: Python version, OS, your `gate.json` (redacted), the exact command and the output.
- Scope we will maintain: the four checks, regex and JUnit result readers, Markdown/JSON output,
  Python 3.9+ on macOS/Linux. Out of scope: generating mutants automatically, sandboxing the test
  command, Windows path semantics (patches welcome but untested by us).

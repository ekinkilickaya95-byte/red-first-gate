# red-first-gate

**Prove that your new tests catch the old bug, and that the tests that stay green have teeth.**

A green test suite says very little about a change. Two cheap questions say much more:

1. Which of the new tests turn **red on the previous version** of the code? A new test that is green there too never saw the bug it claims to catch. At least one must be red; the ones you mark `must_fail_on_baseline` have to be.
2. For every new test that is green on *both* versions: is there a deliberate, hand-written mutant of the new code that turns it red? If nothing can make a test fail, it is not guarding anything.

`red-first-gate` runs your existing test command on two trees (baseline and candidate), classifies every new test, applies anchored mutants to copies of the candidate (all of a mutant's expected tests must turn red), and refuses to go green until every green-on-both test has at least one killer. It also checks the mutants' anchors *before* the long run, so a stale anchor is a one-second error instead of a thirty-minute surprise.

- Zero dependencies. One file. Python 3.9+.
- Works with any language: you supply the test command and either a regex over its output or a JUnit XML path.
- Markdown or JSON reports; non-zero exit on any red check; unreadable or empty results are errors, never passes.

## Who is this for

Anyone shipping a fix with tests and wanting evidence, not optimism: solo developers, small teams, and agents (human or AI) whose "I added tests" claim should be checkable. It is not a mutation-testing framework. Stryker, PIT, mutmut and cosmic-ray generate mutants to measure a score; the mutant step here is the small, hand-written form of that idea. What is different is the combination: running the new tests on the previous version to prove they distinguish the fix, and charging every test that stays green on both versions with a targeted mutant.

## Five-minute try

```sh
git clone <this repository> red-first-gate
cd red-first-gate/example
./try.sh
```

Expected: `anchors`, `known-bad`, `mutants` and `killers` all **GREEN**, exit code 0 (see [`example-output.md`](example-output.md) for the exact output). Then make it red on purpose:

```sh
# drop the only mutant that kills the test which is green on both trees
python3 - <<'EOF'
import json; c = json.load(open("gate.json")); c["mutants"].pop(2); json.dump(c, open("gate.json", "w"), indent=2)
EOF
./try.sh   # killers: RED — "test_cheapest_picks_minimum: green on both trees and no mutant kills it"
git checkout gate.json
```

## The four checks

| Check | Question | Red when |
|---|---|---|
| `anchors` | Does every mutant's `find` text occur exactly once in the candidate (and never in the baseline when `new_code_only` is set)? | any anchor count ≠ 1; a "new code" anchor exists in the baseline |
| `known-bad` | Does every new test pass on the candidate, and is at least one of them red on the baseline? | a new test is red on the candidate; a `must_fail_on_baseline` test is green there; no new test is red on the baseline; the candidate has any red test |
| `mutants` | Does each mutant, applied to a copy of the candidate, make **all** of its `expect` tests red? | an expected test stays green or is missing; every test red under the mutant (a broken suite is not evidence) |
| `killers` | Is every green-on-both test turned red by at least one mutant that was itself killed and did not break the suite? | a green-on-both test has no such killer and is not in `allow_green_on_both` |

`all` runs them in that order and stops at the first red check. An empty result set (the test command printed nothing the parser understood), a test id reported twice (retries, re-runs) and a stale JUnit file are errors (exit 2), never passes.

## Configuration (`gate.json`)

```json
{
  "test_command": ["python3", "-m", "unittest", "-v"],
  "result": {
    "kind": "regex",
    "pattern": "^(?P<id>test_\\w+) \\([^)]*\\)(?: [^.]*)? \\.\\.\\. (?P<status>ok|FAIL|ERROR)$",
    "pass_values": ["ok"]
  },
  "new_tests": ["test_total_discount_capped", "test_cheapest_empty_is_none", "test_cheapest_picks_minimum"],
  "must_fail_on_baseline": ["test_total_discount_capped", "test_cheapest_empty_is_none"],
  "allow_green_on_both": [],
  "mutants": [
    { "id": "m1-discount-not-capped", "file": "cart.py",
      "find": "    capped = min(max(discount_percent, 0), 100)\n",
      "replace": "    capped = discount_percent\n",
      "expect": ["test_total_discount_capped"], "new_code_only": true }
  ],
  "copy_ignore": [".git", "__pycache__", "node_modules"],
  "timeout_s": 600
}
```

- `result.kind: "regex"` — the pattern runs with `re.MULTILINE` over stdout+stderr and must define named groups `id` and `status`; a status in `pass_values` is green, anything else is red.
- `result.kind: "junit"` — `result.path` is a JUnit XML file relative to the tree; test ids are `classname.name`; skipped cases are ignored (they are neither evidence nor failure).
- `new_tests` — ids of the tests added or changed by the candidate. Each must be green on the candidate. Red on the baseline = evidence; green or absent on the baseline = "green on both", which owes a mutant.
- `must_fail_on_baseline` — the subset that must be red on the baseline (hard requirement; use it for regression tests of a fixed bug).
- `mutants[]` — `find`/`replace` are exact text; `expect` lists the test ids that must **all** turn red; `new_code_only: true` adds the check that the anchor does not exist in the baseline (so the mutant really targets new code). `file` must be a relative path inside the tree and a regular file (symlinks are refused); `id` is a slug (`[A-Za-z0-9._-]`).
- `allow_green_on_both` — tests you consciously accept without a killer (write down why in your commit).

## Usage

```sh
python3 red_first_gate.py all       --config gate.json --baseline ../v1 --candidate ../v2 --work /tmp/rfg
python3 red_first_gate.py anchors   --config gate.json --candidate ../v2 --baseline ../v1
python3 red_first_gate.py known-bad --config gate.json --baseline ../v1 --candidate ../v2
python3 red_first_gate.py mutants   --config gate.json --candidate ../v2 --work /tmp/rfg
```

Add `--json` for machine-readable output (`{"check", "ok", ..., "problems": [...]}` per check; test states are `pass`, `fail` or `absent`). Exit codes: `0` green, `1` red, `2` usage, configuration or unreadable results. `--work` must lie outside the candidate tree.

## Known limits

- Mutants are hand-written and anchored to exact text. That is the point (they encode *what the tests must see*), but it means a refactor moves the anchor and `anchors` goes red until you update it.
- Each mutant copies the candidate tree (`copy_ignore` keeps it small) and runs the whole suite. With a slow suite, keep the mutant list short or run `mutants` for a subset by trimming the config.
- The baseline tree must be able to run the candidate's test files; if a new test cannot execute on the baseline (new module, new fixture), it is classified as "green on both" and needs a mutant, which is the honest reading.
- Regex parsing assumes exactly one result line per test id; a repeated id is an error (disable retries).
- Not a sandbox: the test command runs with your permissions in the given trees. On a timeout the whole process group is killed (POSIX); `timeout_s` is per run, not for the whole gate.

## Supported versions

Python 3.9–3.13 on macOS and Linux (CI matrix in `.github/workflows/ci.yml`). Windows is untested.

## Origin

Extracted from the release gate of a mobile game, where every new test was run against the previously shipped build and each test that stayed green on both builds had to be killed by a hand-written mutant before the build shipped. Project-specific parsing, paths and names were removed; the synthetic example replaces the game.

## License

MIT — see [LICENSE](LICENSE). No third-party code is included; see [THIRD_PARTY.md](THIRD_PARTY.md).

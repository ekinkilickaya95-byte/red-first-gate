# red-first-gate: prove new tests catch the old bug and green tests have teeth

**Problem.** A green suite after a fix does not show that the new tests saw the bug, nor that tests which were already green guard anything. Mutation-score tools answer a different question (how many generated mutants die) and are heavy to adopt.

**Honest scope.** Not a mutation-testing framework (Stryker/PIT/mutmut do generated mutants and scores); the new part is the combination of a baseline proof with targeted, hand-written mutants that every green-on-both test must have.

**What this is.** A single-file, zero-dependency gate that runs your existing test command on the previous and the new tree, classifies each new test (red on baseline = evidence; green on both = owes a mutant), applies hand-written anchored mutants to copies of the new tree (all expected tests must turn red), and goes red unless every green-on-both test is killed by at least one mutant that did its own job without breaking the suite. Anchors are validated before the long run.

**Try it (5 minutes).** `cd example && ./try.sh` → four GREEN checks; then remove a mutant from `gate.json` and watch `killers` go RED.

**Tests.** 17 end-to-end tests, each proving one red path (stale anchor, new-code anchor in baseline, must-fail test green on baseline, new test red on candidate, no red on baseline, surviving mutant, partial kill, broken suite under a mutant, green-on-both without killer, allow-list, empty results, duplicated ids, unsafe mutant path, work inside candidate, empty new_tests, JUnit reader, full green path). CI: Python 3.9/3.12/3.13 on ubuntu + macOS.

**Limits.** Hand-written mutants move with refactors; whole-suite run per mutant; not a sandbox. Windows untested.

**Origin.** Extracted from a shipped mobile game's release gate; project-specific parsing and names removed.

**License.** MIT.

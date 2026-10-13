#!/usr/bin/env python3
"""red-first-gate: prove that new tests catch the old bug and that green tests have teeth.

Four checks, one JSON config, one test command run inside each tree:

  anchors    every mutant's `find` text occurs exactly once in the candidate tree (and, when
             `new_code_only` is true, zero times in the baseline tree); cheap, runs first
  known-bad  every test in `new_tests` passes on the candidate; each is then classified:
             red on the baseline = evidence that it catches the old behaviour; green or absent on
             the baseline = "green on both", it owes a killer. `must_fail_on_baseline` lists the
             tests that MUST be red there. At least one new test must be red on the baseline.
  mutants    each mutant is applied to a copy of the candidate; ALL of its `expect` tests must
             turn red there (a surviving mutant means the tests do not see that code path)
  killers    every green-on-both test must be turned red by at least one killed mutant whose run
             did not break the whole suite (unless the test is in `allow_green_on_both`)
  all        anchors -> known-bad -> mutants -> killers, stopping at the first red check

Zero dependencies; Python 3.9+. Results are read from a regex over the test command's output
(named groups `id` and `status`) or from a JUnit XML file. Exit: 0 green, 1 red, 2 usage/config
error or unreadable results.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

EXIT_OK, EXIT_RED, EXIT_USAGE = 0, 1, 2
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


class GateError(Exception):
    """Configuration, usage or unreadable-result problem: exit 2, never a verdict."""


@dataclass
class Mutant:
    id: str
    file: str
    find: str
    replace: str
    expect: list[str]
    new_code_only: bool = False


@dataclass
class Config:
    test_command: list[str]
    result_kind: str  # "regex" | "junit"
    result_pattern: str | None
    result_junit: str | None
    pass_values: set[str]
    new_tests: list[str]
    must_fail_on_baseline: list[str]
    mutants: list[Mutant]
    allow_green_on_both: set[str]
    copy_ignore: list[str]
    timeout_s: int
    path: Path = field(default_factory=Path)


def relative_inside(rel: str, what: str) -> Path:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not rel:
        raise GateError(f"{what} must be a relative path inside the tree without '..': {rel!r}")
    return p


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateError(f"config: cannot read {path}: {exc}")
    result = raw.get("result") or {}
    kind = result.get("kind", "regex")
    if kind not in ("regex", "junit"):
        raise GateError(f"config: result.kind must be 'regex' or 'junit' (got {kind!r})")
    if kind == "regex":
        if not result.get("pattern"):
            raise GateError("config: result.pattern is required for kind 'regex'")
        try:
            rx = re.compile(result["pattern"], re.MULTILINE)
        except re.error as exc:
            raise GateError(f"config: result.pattern is not a valid regex: {exc}")
        if "id" not in rx.groupindex or "status" not in rx.groupindex:
            raise GateError("config: result.pattern must define named groups 'id' and 'status'")
    if kind == "junit":
        if not result.get("path"):
            raise GateError("config: result.path is required for kind 'junit'")
        relative_inside(result["path"], "result.path")
    new_tests = [str(t) for t in raw.get("new_tests", [])]
    if not new_tests:
        raise GateError("config: new_tests must list at least one test id (an empty gate proves nothing)")
    if len(set(new_tests)) != len(new_tests):
        raise GateError("config: new_tests contains duplicates")
    mutants = []
    seen: set[str] = set()
    for m in raw.get("mutants", []):
        for key in ("id", "file", "find", "replace", "expect"):
            if key not in m:
                raise GateError(f"config: mutant missing '{key}': {m}")
        if not SAFE_ID.match(str(m["id"])):
            raise GateError(f"config: mutant id {m['id']!r} must match {SAFE_ID.pattern}")
        if m["id"] in seen:
            raise GateError(f"config: duplicate mutant id {m['id']!r}")
        seen.add(m["id"])
        relative_inside(str(m["file"]), f"mutant {m['id']} file")
        if not isinstance(m["expect"], list) or not m["expect"]:
            raise GateError(f"config: mutant {m['id']!r} needs a non-empty 'expect' list")
        if m["find"] == m["replace"] or not m["find"]:
            raise GateError(f"config: mutant {m['id']!r} does not change anything")
        mutants.append(Mutant(str(m["id"]), str(m["file"]), str(m["find"]), str(m["replace"]), [str(t) for t in m["expect"]], bool(m.get("new_code_only", False))))
    cmd = raw.get("test_command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise GateError("config: test_command must be a non-empty list of strings")
    timeout_s = int(raw.get("timeout_s", 600))
    if timeout_s <= 0:
        raise GateError("config: timeout_s must be positive")
    return Config(
        test_command=cmd,
        result_kind=kind,
        result_pattern=result.get("pattern"),
        result_junit=result.get("path"),
        pass_values={str(v) for v in result.get("pass_values", ["ok", "pass", "passed", "PASS", "PASSED"])},
        new_tests=new_tests,
        must_fail_on_baseline=[str(t) for t in raw.get("must_fail_on_baseline", [])],
        mutants=mutants,
        allow_green_on_both={str(t) for t in raw.get("allow_green_on_both", [])},
        copy_ignore=[str(p) for p in raw.get("copy_ignore", [".git", "__pycache__", "node_modules", ".pytest_cache"])],
        timeout_s=timeout_s,
        path=path,
    )


# ---------------------------------------------------------------- running tests

def run_tests(cfg: Config, tree: Path) -> dict[str, bool]:
    """Run the test command inside `tree`; return {test_id: passed}. Unreadable results are a GateError."""
    junit_path = None
    if cfg.result_kind == "junit":
        junit_path = tree / (cfg.result_junit or "")
        if junit_path.exists():
            junit_path.unlink()  # a stale report from an earlier run must never be read
    posix = os.name == "posix"
    try:
        proc = subprocess.Popen(cfg.test_command, cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                start_new_session=posix)
    except FileNotFoundError as exc:
        raise GateError(f"test command not found: {exc}")
    try:
        output, _ = proc.communicate(timeout=cfg.timeout_s)
    except subprocess.TimeoutExpired:
        if posix:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # the whole process group, not only the shell
            except ProcessLookupError:
                pass
        proc.kill()
        proc.communicate()
        raise GateError(f"test command exceeded {cfg.timeout_s}s in {tree}")
    if cfg.result_kind == "regex":
        results = parse_regex(output or "", cfg.result_pattern or "", cfg.pass_values)
    else:
        results = parse_junit(junit_path, cfg.pass_values)
    if not results:
        tail = "\n".join((output or "").strip().splitlines()[-8:])
        raise GateError(f"no test results could be read in {tree} (exit {proc.returncode}); an empty result set is never a pass. Last output lines:\n{tail}")
    return results


def parse_regex(output: str, pattern: str, pass_values: set[str]) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for m in re.compile(pattern, re.MULTILINE).finditer(output):
        test_id, status = m.group("id"), m.group("status")
        if test_id in results:
            raise GateError(f"test id {test_id!r} reported more than once; a repeated or retried test cannot be scored (make ids unique or disable retries)")
        results[test_id] = status in pass_values
    return results


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # strip an XML namespace if present


def parse_junit(path: Path, pass_values: set[str]) -> dict[str, bool]:
    if not path.is_file():
        raise GateError(f"JUnit file not found after the run: {path}")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise GateError(f"JUnit file is not valid XML: {path}: {exc}")
    results: dict[str, bool] = {}
    for case in root.iter():
        if _local(case.tag) != "testcase":
            continue
        name = case.get("name") or ""
        cls = case.get("classname") or ""
        test_id = f"{cls}.{name}" if cls else name
        kinds = {_local(child.tag) for child in case}
        if "skipped" in kinds:
            continue  # neither evidence nor failure
        if test_id in results:
            raise GateError(f"test id {test_id!r} appears more than once in {path}")
        results[test_id] = not ({"failure", "error"} & kinds)
    return results


# ---------------------------------------------------------------- checks

def check_anchors(cfg: Config, candidate: Path, baseline: Path | None) -> list[str]:
    problems: list[str] = []
    for m in cfg.mutants:
        target = candidate / m.file
        if target.is_symlink():
            problems.append(f"{m.id}: candidate/{m.file} is a symlink; mutants are applied to regular files only")
            continue
        if not target.is_file():
            problems.append(f"{m.id}: file missing in candidate: {m.file}")
            continue
        n = target.read_text(encoding="utf-8").count(m.find)
        if n != 1:
            problems.append(f"{m.id}: anchor occurs {n} times in candidate/{m.file} (must be exactly 1)")
        if baseline is not None and m.new_code_only:
            b = baseline / m.file
            nb = b.read_text(encoding="utf-8").count(m.find) if b.is_file() else 0
            if nb != 0:
                problems.append(f"{m.id}: marked new_code_only but anchor occurs {nb} times in baseline/{m.file}")
    return problems


def status_of(results: dict[str, bool], test_id: str) -> str:
    v = results.get(test_id)
    return "absent" if v is None else ("pass" if v else "fail")


def check_known_bad(cfg: Config, baseline: Path, candidate: Path) -> tuple[dict, list[str]]:
    base = run_tests(cfg, baseline)
    cand = run_tests(cfg, candidate)
    problems: list[str] = []
    catches_old, needs_killer = [], []
    for t in cfg.new_tests:
        cs = status_of(cand, t)
        if cs == "absent":
            problems.append(f"{t}: listed in new_tests but not found in candidate results")
            continue
        if cs == "fail":
            problems.append(f"{t}: red on candidate (new tests must pass on the candidate)")
            continue
        if status_of(base, t) == "fail":
            catches_old.append(t)
        else:
            needs_killer.append(t)  # pass or absent on the baseline: no known-bad evidence
    for t in cfg.must_fail_on_baseline:
        if t not in cfg.new_tests:
            problems.append(f"{t}: must_fail_on_baseline entries must also be listed in new_tests")
        elif status_of(base, t) != "fail":
            problems.append(f"{t}: must fail on baseline but is {status_of(base, t)} there — it does not catch the old behaviour")
    if not catches_old:
        problems.append("no new test is red on the baseline: the suite does not demonstrate the change")
    red_on_candidate = sorted(t for t, ok in cand.items() if not ok)
    if red_on_candidate:
        problems.append("candidate has red tests: " + ", ".join(red_on_candidate))
    report = {
        "baseline": {"total": len(base), "green": sum(base.values())},
        "candidate": {"total": len(cand), "green": sum(cand.values())},
        "new_tests": {t: {"baseline": status_of(base, t), "candidate": status_of(cand, t)} for t in cfg.new_tests},
        "catches_old_behaviour": sorted(catches_old),
        "green_on_both": sorted(needs_killer),
    }
    return report, problems


def apply_mutant(cfg: Config, candidate: Path, work: Path, m: Mutant) -> Path:
    dest = (work / m.id).resolve()
    if dest.parent != work.resolve():
        raise GateError(f"{m.id}: refusing to use a work directory outside --work")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(candidate, dest, ignore=shutil.ignore_patterns(*cfg.copy_ignore), symlinks=True)
    target = dest / m.file
    if target.is_symlink() or not target.is_file():
        raise GateError(f"{m.id}: {m.file} is not a regular file in the copied tree")
    text = target.read_text(encoding="utf-8")
    if text.count(m.find) != 1:
        raise GateError(f"{m.id}: anchor count changed while applying (run 'anchors' first)")
    target.write_text(text.replace(m.find, m.replace), encoding="utf-8")
    (dest / "MUTATION.txt").write_text(f"{m.file}\n--- find ---\n{m.find}\n--- replace ---\n{m.replace}\n", encoding="utf-8")
    return dest


def check_mutants(cfg: Config, candidate: Path, work: Path) -> tuple[dict, list[str]]:
    work.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict] = {}
    problems: list[str] = []
    for m in cfg.mutants:
        tree = apply_mutant(cfg, candidate, work, m)
        results = run_tests(cfg, tree)
        red = sorted(t for t, ok in results.items() if not ok)
        expected = {t: status_of(results, t) for t in m.expect}
        missing = [t for t, s in expected.items() if s == "absent"]
        survived = [t for t, s in expected.items() if s == "pass"]
        killed = not missing and not survived
        suite_broken = bool(results) and len(red) == len(results)  # everything red: setup crash, not evidence
        rows[m.id] = {"expected": expected, "red": red, "killed": killed, "suite_broken": suite_broken}
        if missing:
            problems.append(f"{m.id}: expected tests not found in results: {', '.join(missing)}")
        if survived:
            problems.append(f"{m.id}: SURVIVED — expected tests still green: {', '.join(survived)}")
        if suite_broken:
            problems.append(f"{m.id}: every test is red under this mutant; that is a broken suite, not evidence")
    return {"mutants": rows}, problems


def check_killers(known_bad: dict, mutants: dict, allow: set[str]) -> tuple[dict, list[str]]:
    green = known_bad.get("green_on_both", [])
    killers: dict[str, list[str]] = {t: [] for t in green}
    for mid, row in mutants.get("mutants", {}).items():
        if not row.get("killed") or row.get("suite_broken"):
            continue  # only a mutant that did its own job, without wrecking the suite, can vouch for a test
        for t in row.get("red", []):
            if t in killers:
                killers[t].append(mid)
    problems = [f"{t}: green on both trees and no mutant kills it" for t, k in killers.items() if not k and t not in allow]
    return {"green_on_both": killers, "allowed_without_killer": sorted(allow & set(green))}, problems


# ---------------------------------------------------------------- reporting

def markdown(name: str, payload: dict, problems: list[str]) -> str:
    lines = [f"## {name}: {'GREEN' if not problems else 'RED'}"]
    if name == "known-bad":
        lines.append(f"- baseline: {payload['baseline']['green']}/{payload['baseline']['total']} green · candidate: {payload['candidate']['green']}/{payload['candidate']['total']} green")
        lines.append("| new test | baseline | candidate | evidence |")
        lines.append("|---|---|---|---|")
        for t, r in payload["new_tests"].items():
            ev = "catches old behaviour" if t in payload["catches_old_behaviour"] else ("needs a killer" if t in payload["green_on_both"] else "")
            lines.append(f"| {t} | {r['baseline']} | {r['candidate']} | {ev} |")
    elif name == "mutants":
        lines.append("| mutant | killed | expected tests | other red tests |")
        lines.append("|---|---|---|---|")
        for mid, r in payload["mutants"].items():
            exp = ", ".join(f"{t}={s}" for t, s in r["expected"].items())
            other = ", ".join(t for t in r["red"] if t not in r["expected"]) or "—"
            lines.append(f"| {mid} | {'yes' if r['killed'] else 'NO'} | {exp} | {other} |")
    elif name == "killers":
        lines.append("| green-on-both test | killed by |")
        lines.append("|---|---|")
        for t, k in payload["green_on_both"].items():
            lines.append(f"| {t} | {', '.join(k) or 'NOBODY'} |")
    for p in problems:
        lines.append(f"- ✖ {p}")
    return "\n".join(lines) + "\n"


def emit(args, name: str, payload: dict, problems: list[str]) -> int:
    if args.json:
        print(json.dumps({"check": name, "ok": not problems, **payload, "problems": problems}, indent=1))
    else:
        print(markdown(name, payload, problems))
    return EXIT_OK if not problems else EXIT_RED


# ---------------------------------------------------------------- cli

def run(args) -> int:
    cfg = load_config(args.config)
    cand = args.candidate.resolve() if args.candidate else None
    base = args.baseline.resolve() if args.baseline else None
    work = args.work.resolve() if args.work else None
    if args.check in ("anchors", "known-bad", "mutants", "all") and cand is None:
        raise GateError("--candidate is required")
    if args.check in ("known-bad", "all") and base is None:
        raise GateError("--baseline is required")
    if args.check in ("mutants", "all") and work is None:
        raise GateError("--work is required")
    for name, p in (("candidate", cand), ("baseline", base)):
        if p is not None and not p.is_dir():
            raise GateError(f"--{name} is not a directory: {p}")
    if work is not None and cand is not None and (work == cand or cand in work.parents or work in cand.parents):
        raise GateError("--work must be outside the candidate tree (and the candidate outside --work)")

    if args.check == "anchors":
        return emit(args, "anchors", {"mutants": len(cfg.mutants)}, check_anchors(cfg, cand, base))
    if args.check == "known-bad":
        payload, problems = check_known_bad(cfg, base, cand)
        return emit(args, "known-bad", payload, problems)
    if args.check == "mutants":
        payload, problems = check_mutants(cfg, cand, work)
        return emit(args, "mutants", payload, problems)
    # all
    rc = emit(args, "anchors", {"mutants": len(cfg.mutants)}, check_anchors(cfg, cand, base))
    if rc:
        return rc
    kb, problems = check_known_bad(cfg, base, cand)
    rc = emit(args, "known-bad", kb, problems)
    if rc:
        return rc
    mu, problems = check_mutants(cfg, cand, work)
    rc = emit(args, "mutants", mu, problems)
    if rc:
        return rc
    kl, problems = check_killers(kb, mu, cfg.allow_green_on_both)
    return emit(args, "killers", kl, problems)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="red-first-gate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("check", choices=["anchors", "known-bad", "mutants", "all"])
    ap.add_argument("--config", required=True, type=Path, help="gate.json")
    ap.add_argument("--baseline", type=Path, help="tree of the previous version (known-bad, anchors)")
    ap.add_argument("--candidate", type=Path, help="tree of the new version")
    ap.add_argument("--work", type=Path, help="scratch directory for mutant copies (mutants, all); must be outside the candidate")
    ap.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except GateError as exc:
        print(f"red-first-gate: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())

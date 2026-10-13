"""End-to-end tests for red-first-gate, run against copies of the shipped example.

Each test proves that the gate can turn RED for the failure it is meant to catch; the
green path is tested once. Nothing here depends on the host beyond python3 on PATH.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "red_first_gate.py"
EXAMPLE = ROOT / "example"


def gate(check, tmp, config="gate.json", extra=()):
    cmd = [sys.executable, str(TOOL), check, "--config", str(tmp / config),
           "--baseline", str(tmp / "baseline"), "--candidate", str(tmp / "candidate"),
           "--work", str(tmp / "work"), "--json", *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class ExampleCopy(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        for name in ("baseline", "candidate"):
            shutil.copytree(EXAMPLE / name, self.tmp / name)
        shutil.copy(EXAMPLE / "gate.json", self.tmp / "gate.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def edit_config(self, fn):
        cfg = json.loads((self.tmp / "gate.json").read_text())
        fn(cfg)
        (self.tmp / "gate.json").write_text(json.dumps(cfg))

    def last_report(self, out):
        blocks = [b for b in out.split("\n}\n") if b.strip()]
        return json.loads(blocks[-1] + ("" if blocks[-1].rstrip().endswith("}") else "\n}"))

    # --- green path -------------------------------------------------------
    def test_example_is_green_end_to_end(self):
        rc, out, err = gate("all", self.tmp)
        self.assertEqual(rc, 0, err)
        report = self.last_report(out)
        self.assertEqual(report["check"], "killers")
        self.assertTrue(report["ok"])
        self.assertEqual(report["green_on_both"], {"test_cheapest_picks_minimum": ["m3-cheapest-returns-max"]})

    # --- each failure the gate must catch ---------------------------------
    def test_anchor_occurring_twice_is_red(self):
        src = self.tmp / "candidate" / "cart.py"
        src.write_text(src.read_text() + "\n\ndef unused(prices):\n    return min(prices)\n")
        rc, out, err = gate("anchors", self.tmp)
        self.assertEqual(rc, 1, err)
        self.assertIn("occurs 2 times", out)

    def test_new_code_only_anchor_present_in_baseline_is_red(self):
        self.edit_config(lambda c: c["mutants"][2].update({"new_code_only": True}))  # min(prices) exists in baseline too
        rc, out, _ = gate("anchors", self.tmp)
        self.assertEqual(rc, 1)
        self.assertIn("marked new_code_only", out)

    def test_must_fail_test_that_is_green_on_baseline_is_red(self):
        self.edit_config(lambda c: c["must_fail_on_baseline"].append("test_cheapest_picks_minimum"))
        rc, out, _ = gate("known-bad", self.tmp)
        self.assertEqual(rc, 1)
        self.assertIn("does not catch the old behaviour", out)

    def test_new_test_red_on_candidate_is_red(self):
        src = self.tmp / "candidate" / "cart.py"
        src.write_text(src.read_text().replace("return None", "return 0"))
        rc, out, _ = gate("known-bad", self.tmp)
        self.assertEqual(rc, 1)
        self.assertIn("red on candidate", out)

    def test_suite_with_no_red_on_baseline_is_red(self):
        shutil.copy(self.tmp / "candidate" / "cart.py", self.tmp / "baseline" / "cart.py")  # baseline already fixed
        rc, out, _ = gate("known-bad", self.tmp, extra=())
        self.assertEqual(rc, 1)
        self.assertIn("no new test is red on the baseline", out)

    def test_surviving_mutant_is_red(self):
        # a mutant that only edits a comment cannot be seen by any test
        self.edit_config(lambda c: c["mutants"].append({
            "id": "m4-comment-only", "file": "cart.py",
            "find": "The CANDIDATE fixes both bugs.", "replace": "The CANDIDATE fixes both bugs!",
            "expect": ["test_total_plain"]}))
        rc, out, _ = gate("mutants", self.tmp)
        self.assertEqual(rc, 1)
        self.assertIn("m4-comment-only: SURVIVED", out)

    def test_green_on_both_without_mutant_is_red(self):
        self.edit_config(lambda c: c["mutants"].pop(2))  # drop the only mutant that kills test_cheapest_picks_minimum
        rc, out, _ = gate("all", self.tmp)
        self.assertEqual(rc, 1)
        self.assertIn("no mutant kills it", out)

    def test_allow_list_lets_a_green_on_both_test_through(self):
        self.edit_config(lambda c: (c["mutants"].pop(2), c["allow_green_on_both"].append("test_cheapest_picks_minimum")))
        rc, _, err = gate("all", self.tmp)
        self.assertEqual(rc, 0, err)

    def test_empty_results_are_never_a_pass(self):
        self.edit_config(lambda c: c["result"].update({"pattern": "^(?P<id>never)(?P<status>matches)$"}))
        rc, out, err = gate("known-bad", self.tmp)
        self.assertEqual(rc, 2)
        self.assertIn("empty result set is never a pass", err)

    def test_mutant_must_kill_all_expected_tests(self):
        # m3 flips min->max; it cannot make test_total_plain red, so expecting it too must SURVIVE
        self.edit_config(lambda c: c["mutants"][2]["expect"].append("test_total_plain"))
        rc, out, _ = gate("mutants", self.tmp)
        self.assertEqual(rc, 1)
        self.assertIn("expected tests still green: test_total_plain", out)

    def test_duplicate_test_id_in_output_is_an_error(self):
        runner = self.tmp / "twice.py"
        runner.write_text("import subprocess, sys\nfor _ in range(2): subprocess.run([sys.executable, '-m', 'unittest', '-v'])\n")
        for name in ("baseline", "candidate"):
            shutil.copy(runner, self.tmp / name / "twice.py")
        self.edit_config(lambda c: c.update({"test_command": [sys.executable, "twice.py"]}))
        rc, _, err = gate("known-bad", self.tmp)
        self.assertEqual(rc, 2)
        self.assertIn("reported more than once", err)

    def test_mutant_file_outside_tree_is_rejected(self):
        self.edit_config(lambda c: c["mutants"][0].update({"file": "../outside.py"}))
        rc, _, err = gate("anchors", self.tmp)
        self.assertEqual(rc, 2)
        self.assertIn("must be a relative path inside the tree", err)

    def test_work_inside_candidate_is_rejected(self):
        rc, _, err = gate("mutants", self.tmp, extra=())
        # default work dir is tmp/work (outside) -> fine; now point it inside the candidate
        cmd = [sys.executable, str(TOOL), "mutants", "--config", str(self.tmp / "gate.json"),
               "--candidate", str(self.tmp / "candidate"), "--work", str(self.tmp / "candidate" / "work")]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("outside the candidate tree", proc.stderr)

    def test_broken_suite_under_mutant_is_not_evidence(self):
        # a mutant that breaks the import makes every test red: that is a crash, not a kill
        self.edit_config(lambda c: c["mutants"].append({
            "id": "m5-import-breaks", "file": "cart.py",
            "find": "def total(prices, discount_percent=0):", "replace": "def total(prices, discount_percent=0:",
            "expect": ["test_total_plain"]}))
        rc, out, _ = gate("mutants", self.tmp)
        self.assertEqual(rc, 1)
        self.assertIn("every test is red under this mutant", out)

    def test_empty_new_tests_is_a_config_error(self):
        self.edit_config(lambda c: c.update({"new_tests": []}))
        rc, _, err = gate("anchors", self.tmp)
        self.assertEqual(rc, 2)
        self.assertIn("new_tests must list at least one", err)

    def test_junit_results_are_read(self):
        # same example, results read from a JUnit file produced by a tiny runner
        runner = self.tmp / "run_junit.py"
        runner.write_text(
            "import unittest\n"
            "from xml.sax.saxutils import escape\n"
            "def walk(s):\n"
            "    for x in s:\n"
            "        if isinstance(x, unittest.TestSuite): yield from walk(x)\n"
            "        else: yield x\n"
            "suite = unittest.defaultTestLoader.discover('.')\n"
            "names = [t.id().split('.')[-1] for t in walk(suite)]  # collect BEFORE run: TestSuite.run drops its tests\n"
            "res = unittest.TextTestRunner(stream=open('/dev/null', 'w')).run(suite)\n"
            "bad = {t.id().split('.')[-1] for t, _ in res.failures + res.errors}\n"
            "out = ['<testsuite>']\n"
            "for n in names:\n"
            "    out.append('<testcase classname=\"cart\" name=\"%s\">%s</testcase>' % (escape(n), '<failure/>' if n in bad else ''))\n"
            "out.append('</testsuite>')\n"
            "open('junit.xml', 'w').write('\\n'.join(out))\n")
        self.edit_config(lambda c: (c.update({"test_command": [sys.executable, "run_junit.py"]}),
                                    c["result"].clear(), c["result"].update({"kind": "junit", "path": "junit.xml"}),
                                    c.update({"new_tests": ["cart." + t for t in c["new_tests"]],
                                              "must_fail_on_baseline": ["cart." + t for t in c["must_fail_on_baseline"]]}),
                                    [m["expect"].__setitem__(0, "cart." + m["expect"][0]) for m in c["mutants"]]))
        for name in ("baseline", "candidate"):
            shutil.copy(runner, self.tmp / name / "run_junit.py")
        rc, out, err = gate("all", self.tmp)
        self.assertEqual(rc, 0, err + out)


if __name__ == "__main__":
    unittest.main()

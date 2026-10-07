#!/usr/bin/env python3
"""Run every Velora test suite and print an exact per-module summary.

    python3 tests/run_tests.py              # everything
    python3 tests/run_tests.py --list       # show the suites without running them
    python3 tests/run_tests.py server.tests.test_payments web.tests.test_spa

Exit codes: 0 = all green, 1 = at least one failure or error, 2 = setup problem
(for example a missing Node runtime for the frontend render check).

No pytest, no plugins: the suites are plain ``unittest`` modules, so the runner
only needs the standard library. That keeps `git clone && python3 tests/run_tests.py`
working on a fresh machine.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SUITES = [
    "server.tests.test_migrations",
    "server.tests.test_cli",
    "server.tests.test_accounts",
    "server.tests.test_netutil",
    "server.tests.test_ratelimit",
    "server.tests.test_email",
    "server.tests.test_api",
    "server.tests.test_input_validation",
    "server.tests.test_content",
    "server.tests.test_upload_limits",
    "server.tests.test_payments",
    "server.tests.test_wallets",
    "server.tests.test_multi_asset_payments",
    "server.tests.test_studio",
    "server.tests.test_social_admin",
    "web.tests.test_spa",
    "web.tests.test_frontend",
]


def load_suite(names: list[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in names:
        module = importlib.import_module(name)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


class SummaryResult(unittest.TextTestResult):
    """Collects one line per module so the report is exact, not approximate."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.per_module: dict[str, dict[str, int]] = {}

    def _bucket(self, test) -> dict[str, int]:
        module = test.__class__.__module__
        entry = self.per_module.setdefault(module, {"run": 0, "failures": 0, "errors": 0,
                                                    "skipped": 0})
        return entry

    def startTest(self, test):  # noqa: N802 - unittest API
        super().startTest(test)
        self._bucket(test)["run"] += 1

    def addFailure(self, test, err):  # noqa: N802
        super().addFailure(test, err)
        self._bucket(test)["failures"] += 1

    def addError(self, test, err):  # noqa: N802
        super().addError(test, err)
        self._bucket(test)["errors"] += 1

    def addSkip(self, test, reason):  # noqa: N802
        super().addSkip(test, reason)
        self._bucket(test)["skipped"] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Velora test suites.")
    parser.add_argument("suites", nargs="*", default=None,
                        help="dotted module names (default: every suite)")
    parser.add_argument("--list", action="store_true", help="list the suites and exit")
    parser.add_argument("--verbose", "-v", action="store_true", help="per-test output")
    args = parser.parse_args(argv)

    names = args.suites or SUITES
    if args.list:
        for name in names:
            print(name)
        return 0

    if "--" in names:  # pragma: no cover - defensive
        names = [name for name in names if name != "--"]

    print(f"Velora test run · python {sys.version.split()[0]} · {len(names)} suite(s)")
    print(f"repository: {REPO_ROOT}\n")

    started = time.time()
    suite = load_suite(names)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2 if args.verbose else 1,
                                     resultclass=SummaryResult)
    result = runner.run(suite)
    duration = time.time() - started

    print("\n" + "-" * 66)
    print(f"{'suite':<34}{'run':>6}{'fail':>7}{'error':>7}{'skip':>7}")
    print("-" * 66)
    total = {"run": 0, "failures": 0, "errors": 0, "skipped": 0}
    for module in names:
        entry = result.per_module.get(module)
        if entry is None:
            print(f"{module:<34}{'—':>6}")
            continue
        for key in total:
            total[key] += entry[key]
        print(f"{module:<34}{entry['run']:>6}{entry['failures']:>7}{entry['errors']:>7}"
              f"{entry['skipped']:>7}")
    print("-" * 66)
    print(f"{'TOTAL':<34}{total['run']:>6}{total['failures']:>7}{total['errors']:>7}"
          f"{total['skipped']:>7}")
    print(f"\n{total['run']} tests in {duration:.2f}s")

    if result.wasSuccessful():
        passes = total["run"] - total["skipped"]
        print(f"OK — {passes} passed"
              + (f", {total['skipped']} skipped" if total["skipped"] else ""))
        return 0
    print(f"FAILED — {len(result.failures)} failure(s), {len(result.errors)} error(s)")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

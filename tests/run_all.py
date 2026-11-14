"""Run every test on a throwaway copy of the repo (real data is never touched).

    python tests/run_all.py

Offline: prices and safety checks are simulated. Exit code 1 if anything fails.
"""
import os, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = ["test_backtest.py", "test_scenario.py", "test_recheck.py"]


def main() -> int:
    failed = []
    for name in TESTS:
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "repo")
            shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", "backtest_cache"))
            print(f"=== {name}")
            r = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tests", name), copy])
            if r.returncode:
                failed.append(name)
    print("\n" + ("ALL TESTS PASSED" if not failed else f"FAILED: {', '.join(failed)}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

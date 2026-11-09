"""
Entry point for the "Paper trade" GitHub workflow (.github/workflows/trade.yml),
so you can run trade.py commands from the browser without installing Python.

Inputs come from environment variables (never pasted into a shell command) and
are validated strictly before anything runs:
  CMD          check | add-token | buy | sell | risk | journal | status
  TARGET       contract address (check, add-token) or token id (buy, sell)
  USD          buy amount in USD
  PCT          sell percentage (default 100)
  EXCEPTIONAL  "true" -> buy --exceptional
  FORCE        "true" -> sell --force / add-token --ignore-red-flags

Exit code: 0 on success, 1 when the command was refused or failed (so the
workflow only commits data/ when a trade really happened).
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COMMANDS = {"check", "add-token", "buy", "sell", "risk", "journal", "status"}
CA_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")      # Solana base58 address
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def fail(msg: str) -> None:
    print(f"✗ {msg}")
    sys.exit(1)


def number(raw: str, name: str, lo: float, hi: float) -> str:
    try:
        x = float(raw)
    except (TypeError, ValueError):
        fail(f"{name} must be a number (got {raw!r})")
    if not lo < x <= hi:
        fail(f"{name} must be between {lo} and {hi} (got {x})")
    return repr(x)


def build_argv(env: dict) -> list:
    cmd = (env.get("CMD") or "").strip()
    target = (env.get("TARGET") or "").strip()
    flag = lambda k: (env.get(k) or "").strip().lower() == "true"
    if cmd not in COMMANDS:
        fail(f"unknown command {cmd!r}")
    if cmd in ("check", "add-token"):
        if not CA_RE.match(target):
            fail("target must be a Solana contract address (base58, 32-44 characters)")
        argv = [cmd, target]
        if cmd == "add-token" and flag("FORCE"):
            argv.append("--ignore-red-flags")
        return argv
    if cmd in ("buy", "sell"):
        if not ID_RE.match(target):
            fail("target must be a token id like 'wif' (see `status`)")
        if cmd == "buy":
            argv = ["buy", target, "--usd", number(env.get("USD"), "usd", 0, 100)]
            if flag("EXCEPTIONAL"):
                argv.append("--exceptional")
        else:
            argv = ["sell", target, "--pct", number(env.get("PCT") or "100", "pct", 0, 100)]
            if flag("FORCE"):
                argv.append("--force")
        return argv
    return [cmd]


def main() -> int:
    argv = build_argv(os.environ)
    print(f"$ python scripts/trade.py {' '.join(argv)}\n", flush=True)
    import trade
    try:
        trade.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        # `check` exits 2 on a red flag: that is a valid answer, not a failure
        return 0 if argv[0] == "check" and code == 2 else (1 if code else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())

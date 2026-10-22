import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_hook(agent, stdin_obj, acc_home, cwd=None):
    env = dict(os.environ)
    env["ACC_HOME"] = str(acc_home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "acc", "hook", agent],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or str(REPO_ROOT),
    )
    return proc


def run_cli(args, acc_home, cwd=None, input_text=None):
    env = dict(os.environ)
    env["ACC_HOME"] = str(acc_home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "acc"] + args,
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or str(REPO_ROOT),
    )
    return proc


def parse_hook_context(proc):
    out = proc.stdout.strip()
    if not out:
        return None
    obj = json.loads(out)
    return obj["hookSpecificOutput"]["additionalContext"]

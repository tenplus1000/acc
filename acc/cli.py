"""acc command line interface."""

import argparse
import datetime
import sys
from pathlib import Path

from acc import core, defaults, store
from acc.adapters.base import run_hook
from acc.modules.goals import GoalsError, GoalsModule, ultimate_hash


def cmd_hook(args):
    run_hook(args.agent)


def cmd_context(args):
    # Delegates to core.build_context so the preview uses the exact same
    # resolution (including per-session rapport overrides and the
    # session's fixed project) as the real hook path.
    context = core.build_context(args.session or "", args.cwd or ".", "cli")
    print(context.rstrip("\n"))


def cmd_objective_show(args):
    root = core.get_root()
    goals = GoalsModule(root)
    session = goals.load_session(args.session)
    if session is None:
        print(f"Session {args.session} 尚無資料。")
        return
    status = session.get("status", "unset")
    if status == "active":
        print(f"Objective：{session.get('objective', '')}")
        print(f"Success Criteria：{session.get('success_criteria', '')}")
    else:
        print("目前沒有進行中的 Objective。")


def cmd_objective_set(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        goals.objective_set(args.session, args.objective, args.criteria or "")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Objective 已設定。")


def cmd_objective_revise(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        goals.objective_revise(args.session, args.objective, args.criteria, args.reason or "")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Objective 已修改，修改紀錄已保存。")


def cmd_objective_done(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        session = goals.objective_done(args.session, args.note or "")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Objective 已標記為達成並結案。")
    if session.get("status") == "active":
        print(f"已自動接回：{session.get('objective', '')}")


def cmd_objective_fail(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        session = goals.objective_fail(args.session, args.note or "")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Objective 已標記為失敗並結案。")
    if session.get("status") == "active":
        print(f"已自動接回：{session.get('objective', '')}")


def cmd_objective_park(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        goals.park(args.session, args.reason, actor="agent")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Objective 已擱置。")


def cmd_objective_list(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        text = goals.objective_list_text(args.session)
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(text)


def cmd_objective_amend(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        goals.objective_amend(args.session, args.id, args.to, args.reason or "", note=args.note, actor="agent")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("已更正。")


def cmd_objective_log(args):
    root = core.get_root()
    goals = GoalsModule(root)
    work_from, work_to = args.work_from, args.work_to
    if args.latest_work:
        sessions = goals.work_sessions()
        if sessions:
            work_from = work_to = sessions[-1]["index"]
    try:
        text = goals.work_log(args.session, work_from=work_from, work_to=work_to)
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(text.rstrip("\n"))


def cmd_work_status(args):
    root = core.get_root()
    goals = GoalsModule(root)
    state = goals.work_status()
    if state.get("working", True):
        print(f"工作中（第 {state.get('index')} 次工作階段，{state.get('since')} 起）")
    else:
        print(f"休息中（第 {state.get('index')} 次工作階段結束後，{state.get('since')} 起）")


def cmd_work_start(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        state = goals.work_start(actor="user")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"已切回工作中（第 {state.get('index')} 次工作階段）。")


def cmd_work_stop(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        goals.work_stop(actor="user")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("已切到休息中。")


def cmd_super_suggest(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        project, entry = goals.suggest_super(args.session, args.suggestion, args.reason or "")
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"已提出建議，等待使用者決定（專案：{project}）。")


def cmd_super_show(args):
    root = core.get_root()
    goals = GoalsModule(root)
    try:
        project, super_objective, pending = goals.super_for_session(args.session)
    except GoalsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"專案：{project}")
    print(f"目前的 Super Objective：{super_objective or '（尚未設定）'}")
    if pending:
        print(f"待決定的建議（{len(pending)} 則）：")
        for s in pending:
            print(f"- {s['suggestion']}（理由：{s.get('reason', '')}）")
    else:
        print("目前沒有待決定的建議。")


RULES_START = "<!-- acc:rules start -->"
RULES_END = "<!-- acc:rules end -->"
RULES_TARGETS = {
    "claude": Path.home() / ".claude" / "CLAUDE.md",
    "codex": Path.home() / ".codex" / "AGENTS.md",
}


def rules_path(root):
    return Path(root) / "templates" / "rules.md"


def cmd_rules_show(args):
    root = core.get_root()
    store.ensure_seeded(root)
    print(store.read_text(rules_path(root), defaults.RULES_MD).rstrip("\n"))


def install_rules(agent, root, target=None):
    """Write the managed rules block into the agent's global instruction file.

    Claude Code imports both the rules file and goals/ultimate.md live
    (edits take effect without reinstalling, including Ultimate changes);
    Codex has no import syntax, so rules.md + ultimate.md are copied in --
    changes to either need a re-install to reach an already-open Codex
    conversation.

    Also records, in `<root>/.rules_installed.json`, what Ultimate text
    this agent's file now carries (used to compute a stale-Codex-copy
    session's starting baseline -- see GoalsModule._new_session_ultimate_baseline).
    """
    store.ensure_seeded(root)
    root = Path(root)
    target = Path(target) if target else RULES_TARGETS[agent]
    if agent == "claude":
        body = f"@{rules_path(root)}\n@{root / 'goals' / 'ultimate.md'}"
    else:
        rules_text = store.read_text(rules_path(root), defaults.RULES_MD).rstrip("\n")
        ultimate_text = store.read_text(root / "goals" / "ultimate.md", defaults.ULTIMATE_MD).rstrip("\n")
        body = rules_text + "\n\n" + ultimate_text
    block = f"{RULES_START}\n{body}\n{RULES_END}"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if RULES_START in existing and RULES_END in existing:
        head, rest = existing.split(RULES_START, 1)
        _, tail = rest.split(RULES_END, 1)
        text = head + block + tail
    else:
        text = (existing.rstrip("\n") + "\n\n" if existing.strip() else "") + block + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    store.atomic_write_text(target, text)

    ult = GoalsModule(root).load_ultimate()
    h = ultimate_hash(ult["ultimate_goal"], ult["ultimate_definition"])
    installed_path = root / ".rules_installed.json"
    installed = store.read_json(installed_path, None) or {}
    installed[agent] = {
        "ultimate_hash": h,
        "target": str(target),
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    store.atomic_write_json(installed_path, installed)
    return target


def cmd_rules_install(args):
    target = install_rules(args.agent, core.get_root(), args.target)
    print(f"已寫入 {target}")


def cmd_serve(args):
    from acc.web.server import serve

    serve(host=args.host, port=args.port)


def cmd_root(args):
    print(str(core.get_root()))


def build_parser():
    parser = argparse.ArgumentParser(prog="acc", description="Agent Context Controller")
    sub = parser.add_subparsers(dest="command", required=True)

    p_hook = sub.add_parser("hook", help="run a UserPromptSubmit hook")
    p_hook.add_argument("agent", choices=["claude", "codex"])
    p_hook.set_defaults(func=cmd_hook)

    p_context = sub.add_parser("context", help="preview the context for a session")
    p_context.add_argument("--session", default="")
    p_context.add_argument("--cwd", default=".")
    p_context.set_defaults(func=cmd_context)

    p_obj = sub.add_parser("objective", help="manage the current session objective")
    obj_sub = p_obj.add_subparsers(dest="objective_command", required=True)

    p_show = obj_sub.add_parser("show")
    p_show.add_argument("--session", required=True)
    p_show.set_defaults(func=cmd_objective_show)

    p_set = obj_sub.add_parser("set")
    p_set.add_argument("objective")
    p_set.add_argument("--criteria", default="")
    p_set.add_argument("--session", required=True)
    p_set.set_defaults(func=cmd_objective_set)

    p_revise = obj_sub.add_parser("revise")
    p_revise.add_argument("objective", nargs="?", default=None)
    p_revise.add_argument("--criteria", default=None)
    p_revise.add_argument("--reason", required=True)
    p_revise.add_argument("--session", required=True)
    p_revise.set_defaults(func=cmd_objective_revise)

    p_done = obj_sub.add_parser("done")
    p_done.add_argument("--note", default="")
    p_done.add_argument("--session", required=True)
    p_done.set_defaults(func=cmd_objective_done)

    p_fail = obj_sub.add_parser("fail")
    p_fail.add_argument("--note", default="")
    p_fail.add_argument("--session", required=True)
    p_fail.set_defaults(func=cmd_objective_fail)

    p_park = obj_sub.add_parser("park")
    p_park.add_argument("--reason", required=True)
    p_park.add_argument("--session", required=True)
    p_park.set_defaults(func=cmd_objective_park)

    p_list = obj_sub.add_parser("list")
    p_list.add_argument("--session", required=True)
    p_list.set_defaults(func=cmd_objective_list)

    p_amend = obj_sub.add_parser("amend")
    p_amend.add_argument("--session", required=True)
    p_amend.add_argument("--id", required=True)
    p_amend.add_argument("--to", required=True, choices=["achieved", "failed", "parked", "active"])
    p_amend.add_argument("--reason", required=True)
    p_amend.add_argument("--note", default=None)
    p_amend.set_defaults(func=cmd_objective_amend)

    p_log = obj_sub.add_parser("log")
    p_log.add_argument("--session", required=True)
    p_log.add_argument("--work-from", type=int, default=None)
    p_log.add_argument("--work-to", type=int, default=None)
    p_log.add_argument("--latest-work", action="store_true")
    p_log.set_defaults(func=cmd_objective_log)

    p_super = sub.add_parser("super", help="suggest/inspect the project's Super Objective")
    super_sub = p_super.add_subparsers(dest="super_command", required=True)

    p_super_suggest = super_sub.add_parser("suggest")
    p_super_suggest.add_argument("suggestion")
    p_super_suggest.add_argument("--reason", default="")
    p_super_suggest.add_argument("--session", required=True)
    p_super_suggest.set_defaults(func=cmd_super_suggest)

    p_super_show = super_sub.add_parser("show")
    p_super_show.add_argument("--session", required=True)
    p_super_show.set_defaults(func=cmd_super_show)

    p_work = sub.add_parser("work", help="工作／休息開關（使用者自己的紀錄，不影響注入內容）")
    work_sub = p_work.add_subparsers(dest="work_command", required=True)
    p_work_status = work_sub.add_parser("status")
    p_work_status.set_defaults(func=cmd_work_status)
    p_work_start = work_sub.add_parser("start")
    p_work_start.set_defaults(func=cmd_work_start)
    p_work_stop = work_sub.add_parser("stop")
    p_work_stop.set_defaults(func=cmd_work_stop)

    p_rules = sub.add_parser("rules", help="show or install the agent rules")
    rules_sub = p_rules.add_subparsers(dest="rules_command", required=True)
    p_rules_show = rules_sub.add_parser("show")
    p_rules_show.set_defaults(func=cmd_rules_show)
    p_rules_install = rules_sub.add_parser("install")
    p_rules_install.add_argument("agent", choices=sorted(RULES_TARGETS))
    p_rules_install.add_argument("--target", default=None)
    p_rules_install.set_defaults(func=cmd_rules_install)

    p_serve = sub.add_parser("serve", help="run the local web UI")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.set_defaults(func=cmd_serve)

    p_root = sub.add_parser("root", help="print the data root path")
    p_root.set_defaults(func=cmd_root)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

"""Command line: python -m landmark3d [serve|setup|doctor|new|run|status|approve]"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="landmark3d")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="start the local web app")
    s.add_argument("--port", type=int, default=8770)
    sub.add_parser("doctor", help="check Blender, MCP, Node, AI connection")
    su = sub.add_parser("setup", help="download the viewer files, find Blender, run the environment check")
    su.add_argument("--force", action="store_true", help="download the viewer files again")
    n = sub.add_parser("new", help="create a project")
    n.add_argument("name")
    n.add_argument("location", nargs="?", default="")
    r = sub.add_parser("run", help="run until the next approval / missing data")
    r.add_argument("project")
    st = sub.add_parser("status")
    st.add_argument("project")
    a = sub.add_parser("approve")
    a.add_argument("project")
    a.add_argument("phase")
    a.add_argument("--changes", default=None, help="request changes with this note instead of approving")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve
        print(f"Landmark3D web app: http://127.0.0.1:{args.port}")
        serve(port=args.port)
        return
    if args.cmd == "setup":
        from . import installer
        raise SystemExit(installer.main(["--force"] if args.force else []))
    from . import doctor, pipeline
    from .project import Project
    if args.cmd == "doctor":
        rep = doctor.run_all()
        for c in rep["checks"]:
            flag = "OK " if c["ok"] else ("opt" if c.get("optional") else "ERR")
            print(f"[{flag}] {c['id']}: {c['detail']}" + (f"\n      fix: {c['fix']}" if c.get("fix") else ""))
        print("READY" if rep["ok"] else "NOT READY")
    elif args.cmd == "new":
        print(Project.create(args.name, args.location).id)
    elif args.cmd == "run":
        print(json.dumps(pipeline.run_until_blocked(Project(args.project)), ensure_ascii=False))
    elif args.cmd == "status":
        stt = Project(args.project).state()
        for pid, ph in stt["phases"].items():
            print(f"{pid:15s} {ph['status']:18s} {ph['message']}")
        if stt.get("missing"):
            print("missing:", ", ".join(i["id"] for i in stt["missing"]))
    elif args.cmd == "approve":
        prj = Project(args.project)
        try:
            print(pipeline.approve(prj, args.phase, "changes_requested" if args.changes else "approved", args.changes or ""))
        except pipeline.ApprovalError as exc:                 # the same rule as the web API
            print(f"not approved: {exc}", file=sys.stderr)
            raise SystemExit(2)


if __name__ == "__main__":
    main()

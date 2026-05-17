"""Command-line entrypoints. ``python -m aicrew.cli <cmd>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import db
from .api import serve
from .logging_setup import configure
from .pipeline import PipelineRunner
from .seed import seed
from .settings import load_settings


def main(argv: list[str] | None = None) -> int:
    configure(logging.INFO)
    parser = argparse.ArgumentParser(prog="aicrew")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create database schema")
    p_init.add_argument("--db", default=None)

    p_seed = sub.add_parser("seed", help="seed demo project")
    p_seed.add_argument("--db", default=None)

    p_serve = sub.add_parser("serve", help="run API + UI server")
    p_serve.add_argument("--port", type=int, default=None)

    p_demo = sub.add_parser("demo", help="init+seed+run one full pipeline; print summary")
    p_demo.add_argument("--db", default=None)

    args = parser.parse_args(argv)
    settings = load_settings()
    if getattr(args, "db", None):
        import os

        os.environ["AICREW_DB"] = args.db
        settings = load_settings()
    if getattr(args, "port", None):
        import os

        os.environ["AICREW_PORT"] = str(args.port)
        settings = load_settings()

    if args.cmd == "init":
        db.init_schema(settings.db_path)
        print(f"initialized: {settings.db_path}")
        return 0
    if args.cmd == "seed":
        info = seed(settings)
        print(json.dumps(info, indent=2))
        return 0
    if args.cmd == "serve":
        serve(settings)
        return 0
    if args.cmd == "demo":
        info = seed(settings)
        runner = PipelineRunner(settings)
        summary = runner.run_full(info["project_id"])
        print("=" * 50)
        print(f"Project   : {info['project_id']} ({info['status']})")
        print(f"Topics    : {summary.topics_generated} ({summary.topics_validated} ranked)")
        print(f"Articles  : {summary.articles_written}")
        print(f"Posts     : {summary.posts_created} created / {summary.posts_published} published")
        print(f"Total cost: ${summary.cost_usd:.4f}")
        print("=" * 50)
        print("UI:       make api  ->  http://localhost:8000/")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

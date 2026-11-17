"""mc-bridge serve | ask | daily-check | new-secret

  mc-bridge serve                    run the HTTPS endpoint locally (put TLS in front, or deploy to Render)
  mc-bridge ask "how much is it?"    one question through the bridge, printed as ManyChat JSON
  mc-bridge daily-check              empty-run alert if no AI calls today (run once a day)
  mc-bridge new-secret               print a fresh random shared secret for BRIDGE_SECRETS
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys

from . import logs
from .config import Settings
from .inbound import parse


def main(argv=None):
    p = argparse.ArgumentParser(prog="mc-bridge")
    p.add_argument("--config", default=os.environ.get("BRIDGE_CONFIG", "config/bridge.yaml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    a = sub.add_parser("ask")
    a.add_argument("text")
    a.add_argument("--channel", default="instagram")
    a.add_argument("--mode", choices=["dynamic", "external"], default="dynamic")
    a.add_argument("--contact", default="1000001")
    sub.add_parser("daily-check")
    sub.add_parser("new-secret")
    args = p.parse_args(argv)
    logs.setup(os.environ.get("LOG_LEVEL", "INFO"))

    if args.cmd == "new-secret":
        print(secrets.token_urlsafe(32))
        return 0
    if args.cmd == "serve":
        import uvicorn

        os.environ["BRIDGE_CONFIG"] = args.config
        uvicorn.run("mc_bridge.server:app_from_env", factory=True, host=args.host, port=args.port)
        return 0

    from .server import build_bridge

    settings = Settings.load(args.config)
    bridge = build_bridge(settings)
    if args.cmd == "daily-check":
        n = bridge.daily_check()
        print(f"AI calls today: {n}")
        return 0 if n else 1
    inbound = parse({"channel": args.channel, "contact": {"id": args.contact, "last_input_text": args.text}},
                    settings.default_channel, settings.max_input_chars)
    print(json.dumps(bridge.respond(inbound, args.mode), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

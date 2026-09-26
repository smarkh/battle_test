"""Serve the web UI on this machine: python -m battle_test.web [--demo-model]"""

import argparse
import sys
from pathlib import Path

import uvicorn

from battle_test.config import DEFAULT_CONFIG_PATH, load_web_config
from battle_test.web.app import create_app
from battle_test.web.demo import DemoClient

# No login until step 5 part 2, so nothing else may reach this server.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="battle_test.web", description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--port", type=int, help="Override web.port.")
    parser.add_argument("--demo-model", action="store_true",
                        help="Use canned drafts instead of Ollama (for working on the UI without a GPU).")
    args = parser.parse_args(argv)

    web = load_web_config(args.config)
    if web.host not in LOCAL_HOSTS:
        print(f"error: web.host is {web.host!r}. The web UI has no login yet, so it only serves "
              "on 127.0.0.1 / localhost.", file=sys.stderr)
        return 1
    app = create_app(args.config, client=DemoClient() if args.demo_model else None,
                     model_label="demo (canned drafts)" if args.demo_model else None)
    print(f"battle_test web UI: http://{web.host}:{args.port or web.port}"
          + ("  (demo model)" if args.demo_model else ""), file=sys.stderr)
    uvicorn.run(app, host=web.host, port=args.port or web.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())

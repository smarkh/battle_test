"""Serve the web UI: python -m battle_test.web [--demo-model]

On the laptop it serves on 127.0.0.1 only. The deployed server (config
.server.toml, behind Caddy + Cloudflare) may serve on 0.0.0.0 inside its
container, but only when [web] has both behind_proxy and secure_cookies on.
"""

import argparse
import sys
from pathlib import Path

import uvicorn

from battle_test.config import DEFAULT_CONFIG_PATH, load_web_config
from battle_test.web.app import create_app
from battle_test.web.demo import DemoClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="battle_test.web", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--port", type=int, help="Override web.port.")
    parser.add_argument("--demo-model", action="store_true",
                        help="Use canned drafts instead of Ollama (for working on the UI without a GPU).")
    args = parser.parse_args(argv)

    web = load_web_config(args.config)
    problem = web.public_serving_problem()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    app = create_app(args.config, client=DemoClient() if args.demo_model else None,
                     model_label="demo (canned drafts)" if args.demo_model else None)
    port = args.port or web.port
    print(f"battle_test web UI: http://{web.host}:{port}" + ("  (demo model)" if args.demo_model else "")
          + ("  (behind proxy)" if web.behind_proxy else ""), file=sys.stderr)
    uvicorn.run(
        app, host=web.host, port=port, log_level="warning",
        # Behind Caddy, trust its X-Forwarded-* headers (the real client
        # address and scheme). The container publishes no port, so only
        # Caddy on the Docker network can reach it.
        proxy_headers=web.behind_proxy, forwarded_allow_ips="*" if web.behind_proxy else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

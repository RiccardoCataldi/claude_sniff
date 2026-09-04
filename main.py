#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging
from http.server import ThreadingHTTPServer
from pathlib import Path

from capture import CaptureAddon, ConversationStore, ca_cert_path, create_master
from policy import Policy
from ui import serve_blocking, start_ui

log = logging.getLogger("claude-sniff")
DEFAULT_UI_PORT = 8765
DEFAULT_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.getLogger("mitmproxy").setLevel(logging.WARNING)


def print_setup(port: int) -> None:
    cert = ca_cert_path()
    proxy = f"http://127.0.0.1:{port}"
    log.info("MITM listening on %s (loopback only)", proxy)
    log.info("CA: %s", cert)
    log.info("Proxy and intercept start OFF. Pin clients once, then toggle in the UI.")
    log.info("")
    log.info("Claude Code / any client:")
    log.info("    export HTTP_PROXY=%s HTTPS_PROXY=%s", proxy, proxy)
    log.info("    export NODE_EXTRA_CA_CERTS=%s", cert)
    log.info("    export SSL_CERT_FILE=%s REQUESTS_CA_BUNDLE=%s AWS_CA_BUNDLE=%s", cert, cert, cert)
    log.info("    claude")
    log.info("")
    log.info("With proxy OFF, LLM HTTPS is tunneled (no mitmproxy CA). Trust the CA only when proxy is ON.")


def _launch_ui(
    logs_dir: Path,
    port: int | None,
    projects_dir: Path | None,
    control: CaptureAddon | None,
) -> ThreadingHTTPServer | None:
    if port is None:
        return None
    try:
        httpd = start_ui(logs_dir, port, projects_dir, control)
    except OSError as exc:
        log.warning("UI not started on :%d — %s", port, exc)
        return None
    log.info("UI: http://127.0.0.1:%d", httpd.server_address[1])
    return httpd


def _stop_ui(httpd: ThreadingHTTPServer | None) -> None:
    if httpd is None:
        return
    httpd.shutdown()
    httpd.server_close()


async def wait_for_port(master, timeout: float = 10.0) -> int:
    proxyserver = master.addons.get("proxyserver")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        addrs = proxyserver.listen_addrs()
        if addrs:
            return int(addrs[0][1])
        await asyncio.sleep(0.05)
    raise RuntimeError("Proxy did not start in time")


async def cmd_watch(
    port: int,
    logs_dir: Path,
    ui_port: int | None,
    projects_dir: Path | None,
    aws_profile: str | None = None,
) -> None:
    store = ConversationStore(logs_dir)
    addon = CaptureAddon(store, policy=Policy(), aws_profile=aws_profile)
    master = create_master("127.0.0.1", port, addon)
    task = asyncio.create_task(master.run())
    httpd = None
    try:
        bound = await wait_for_port(master)
        addon.listen_url = f"http://127.0.0.1:{bound}"
        httpd = _launch_ui(logs_dir, ui_port, projects_dir, addon)
        print_setup(bound)
        log.info("Logs in %s/<client>/<session>.jsonl", logs_dir.resolve())
        await task
    except asyncio.CancelledError:
        master.shutdown()
        raise
    except KeyboardInterrupt:
        master.shutdown()
        await asyncio.sleep(0.2)
    finally:
        _stop_ui(httpd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MITM for Claude Code on Bedrock")
    parser.add_argument("--port", type=int, default=None, help="MITM port (default: 9090)")
    parser.add_argument("--logs-dir", type=Path, default=Path("logs"), help="Log directory (default: logs)")
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT, help=f"Monitoring UI port (default: {DEFAULT_UI_PORT})")
    parser.add_argument("--no-ui", action="store_true", help="Do not start the monitoring UI")
    parser.add_argument(
        "--claude-projects-dir",
        type=Path,
        default=DEFAULT_CLAUDE_PROJECTS,
        help="Claude Code diary root (default: ~/.claude/projects)",
    )
    parser.add_argument("--aws-profile", default=None, help="AWS profile used to re-sign edited Bedrock requests")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("ui", help="Serve the monitoring UI only (no MITM)")
    return parser.parse_args()


def main() -> None:
    _setup_logging()
    args = parse_args()
    logs_dir: Path = args.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    projects_dir: Path = args.claude_projects_dir
    ui_port = None if args.no_ui else args.ui_port

    if args.cmd == "ui":
        try:
            serve_blocking(logs_dir, args.ui_port, projects_dir)
        except KeyboardInterrupt:
            log.info("Stop")
        return

    port = 9090 if args.port is None else args.port
    try:
        asyncio.run(cmd_watch(port, logs_dir, ui_port, projects_dir, args.aws_profile))
    except KeyboardInterrupt:
        log.info("Stop")


if __name__ == "__main__":
    main()

"""Command line: `kairos serve` (HTTP: REST + /mcp) or `kairos mcp-stdio` (local MCP clients)."""

from __future__ import annotations

import argparse
import sys

import anyio


def _serve() -> None:
    import uvicorn

    from kairos.app import create_app
    from kairos.settings import load_settings

    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        proxy_headers=True,
        timeout_graceful_shutdown=10,
    )


def _stdio() -> None:
    from kairos.api.mcp import build_mcp
    from kairos.observability import setup_logging
    from kairos.service import CalendarService
    from kairos.settings import load_settings

    settings = load_settings()
    setup_logging(settings.log_level, stream=sys.stderr)  # stdout carries the MCP protocol
    service = CalendarService(settings)
    mcp = build_mcp(service, settings)

    async def run() -> None:
        await service.start()
        try:
            await mcp.run_stdio_async()
        finally:
            await service.close()

    anyio.run(run)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kairos", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Run the HTTP server: REST API and MCP at /mcp (default)")
    sub.add_parser("mcp-stdio", help="Run as a local MCP server over stdio")
    args = parser.parse_args(argv)

    from kairos.settings import ConfigError

    try:
        if args.command == "mcp-stdio":
            _stdio()
        else:
            _serve()
    except ConfigError as exc:
        print(f"kairos: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()

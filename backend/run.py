#!/usr/bin/env python
"""
Subtitle Translator Backend Launcher

Starts the FastAPI server with uvicorn.
Handles warmup, signal handling, and graceful shutdown.

Usage:
    python run.py                    # Default settings
    python run.py --host 0.0.0.0     # Bind to all interfaces
    python run.py --port 8080        # Custom port
    python run.py --no-warmup        # Skip model warmup
    python run.py --debug            # Enable debug mode
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from app.config import settings


def setup_logging(debug: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if debug else logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )
    
    # Reduce noise from third-party libraries
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('transformers').setLevel(logging.WARNING)
    logging.getLogger('torch').setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Subtitle Translator Backend Server',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        '--host',
        type=str,
        default=settings.server.host,
        help='Host to bind to'
    )
    
    parser.add_argument(
        '--port',
        type=int,
        default=settings.server.port,
        help='Port to bind to'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        default=settings.server.debug,
        help='Enable debug mode'
    )
    
    parser.add_argument(
        '--no-warmup',
        action='store_true',
        help='Skip model warmup on startup'
    )
    
    parser.add_argument(
        '--fast-mode',
        action='store_true',
        help='Enable fast mode (skip post-editor)'
    )
    
    parser.add_argument(
        '--reload',
        action='store_true',
        help='Enable auto-reload on code changes (development only)'
    )
    
    return parser.parse_args()


def print_banner(host: str, port: int) -> None:
    """Print startup banner."""
    banner = f"""
╔══════════════════════════════════════════════════════════════════╗
║                 SUBTITLE TRANSLATOR BACKEND                     ║
║              Live captions + translation, local-first            ║
╠══════════════════════════════════════════════════════════════════╣
║  Server:       http://{host}:{port}                              
║  WebSocket:    ws://{host}:{port}/ws                             
║  Debug UI:     http://{host}:{port}/debug                        
║  Health:       http://{host}:{port}/health                       
╠══════════════════════════════════════════════════════════════════╣
║  Target Languages: {', '.join(settings.translation.target_languages)}
║  ASR Languages:    {', '.join(settings.asr.languages)}
║  Device:           {settings.translation.device}
║  Refiner:          {'Enabled' if settings.refiner.enabled else 'Disabled (fast mode)'}
╚══════════════════════════════════════════════════════════════════╝
"""
    print(banner)


def run_server(args: argparse.Namespace) -> None:
    """Run the uvicorn server."""
    # Apply command line overrides
    if args.fast_mode:
        settings.features.fast_mode = True
        settings.refiner.enabled = False
    
    if args.debug:
        settings.server.debug = True
    
    if args.no_warmup:
        # Warmup happens inside the app lifespan (main.py); this just disables it.
        settings.features.warmup_on_start = False

    print_banner(args.host, args.port)
    
    # Configure uvicorn
    config = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="debug" if args.debug else "info",
        access_log=args.debug,
        ws_ping_interval=settings.server.ws_ping_interval,
        ws_ping_timeout=settings.server.ws_ping_timeout,
    )
    
    server = uvicorn.Server(config)
    
    # Handle signals for graceful shutdown
    def signal_handler(signum, frame):
        logger = logging.getLogger('server')
        logger.info(f"Received signal {signum}, shutting down...")
        server.should_exit = True
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run server
    server.run()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    setup_logging(args.debug)
    
    logger = logging.getLogger('main')
    logger.info("Starting Subtitle Translator Backend...")
    
    try:
        run_server(args)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)
    
    logger.info("Server stopped")


if __name__ == "__main__":
    main()

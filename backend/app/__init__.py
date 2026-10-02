"""
Subtitle Translator Backend
===========================

A FastAPI-based backend for real-time dual-language subtitle translation.

Components:
- translation/ - Translation pipeline (MT, post-editing, language detection)
- cache/ - Caching layers (memory LRU, SQLite translation memory)
- websocket_handler.py - WebSocket connection management
- main.py - FastAPI application entry point
- config.py - Configuration management
- models.py - Pydantic data models
"""

__version__ = "1.0.0"
__author__ = "Subtitle Translator Team"

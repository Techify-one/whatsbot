"""WhatsBot — WhatsApp AI Bot with Web GUI."""

import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from config.settings import get_data_dir


def main():
    data_dir = get_data_dir()

    # Initialize database before anything else.
    # URL resolution: DATABASE_URL env > storages/database.json > sqlite default.
    from db import init_db
    is_docker = os.environ.get("WHATSBOT_DOCKER") == "1" or Path("/.dockerenv").exists()
    storages_dir = data_dir / "storages"
    storages_dir.mkdir(exist_ok=True)
    init_db(storages_dir=storages_dir)

    # Auto-migrate from JSON files if DB is empty
    from db.migrate_json import needs_migration, migrate
    if needs_migration(data_dir):
        migrate(data_dir)

    # Now load settings (reads from SQLite)
    from config.settings import Settings
    settings = Settings()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                settings.logs_dir / "whatsbot.log",
                encoding="utf-8",
            ),
        ],
    )
    logger = logging.getLogger("whatsbot")
    logger.info("WhatsBot starting...")

    from gowa.manager import GOWAManager
    from gowa.client import GOWAClient
    from agent.handler import AgentHandler
    from server.app import create_app

    port = settings.get("gowa_port", 3000)
    web_port = settings.get("web_port", 8080)

    webhook_url = f"http://127.0.0.1:{web_port}/api/webhook"
    gowa_manager = GOWAManager(port=port, data_dir=settings.data_dir, webhook_url=webhook_url)
    gowa_client = GOWAClient(port=port)

    agent_handler = AgentHandler(
        api_key=settings.get("openrouter_api_key", ""),
        system_prompt=settings.get("system_prompt", "Você é um assistente útil."),
        max_context_messages=settings.get("max_context_messages", 10),
        inactivity_timeout_min=settings.get("inactivity_timeout_min", 30),
        model=settings.get("model", "deepseek/deepseek-v4-pro"),
        audio_model=settings.get("audio_model", "google/gemini-3-flash-preview"),
        image_model=settings.get("image_model", "google/gemini-3-flash-preview"),
        document_model=settings.get("document_model", "google/gemini-2.5-flash"),
        default_ai_enabled=settings.get("default_ai_enabled", True),
        multi_agent_enabled=settings.get("multi_agent_enabled", False),
        agent_team_mode=settings.get("agent_team_mode", "coordinate"),
        agents=settings.get("agents", []),
        ai_engine_enabled=settings.get("ai_engine_enabled", False),
    )

    app = create_app(
        settings=settings,
        gowa_manager=gowa_manager,
        gowa_client=gowa_client,
        agent_handler=agent_handler,
    )

    host = "0.0.0.0"

    # Open browser after server has time to start (skip in Docker — no display)
    if not is_docker:
        url = f"http://127.0.0.1:{web_port}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import uvicorn
    logger.info("Starting web server on http://%s:%d", host, web_port)
    uvicorn.run(app, host=host, port=web_port, log_level="warning")
    logger.info("WhatsBot exiting.")


if __name__ == "__main__":
    main()

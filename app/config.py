import json
import logging
import os
from typing import Any, Dict

from dictdiffer import diff
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Get the base directory for configs
if os.environ.get("DOCKER_ENV") == "true":  # Docker environment
    CONFIG_BASE = "/app/configs"
else:  # Local environment
    CONFIG_BASE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs"
    )

DEFAULT_CONFIG = {
    "server": "http://localhost:8000",
    "endpoint": "/v1/chat/completions",
    "model": "gpt-3.5-turbo",
    "apikey": "",
    "target_language": "en",
    "chat": True,
    "systemmessages": True,
    "redis_url": "redis://localhost:6379",  # Default for local development
    "gemini_model": "gemini-2.0-flash-exp",
    "gemini_apikey": "",
}


class Config:
    def __init__(self, config_path: str | None = None):
        if config_path is None:
            config_path = os.path.join(CONFIG_BASE, "config.json")
        self.config_path = config_path
        self.config: Dict[Any, Any] = {}
        self.load_config()
        self._setup_watchdog()

    def _setup_watchdog(self) -> None:
        observer = Observer()
        handler = FileSystemEventHandler()
        handler.on_modified = (
            lambda event: self.load_config()
            if event.src_path == self.config_path
            else None
        )
        observer.schedule(handler, os.path.dirname(self.config_path))
        observer.start()

    def load_config(self) -> None:
        """Load config from file or create default if not exists"""
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            self.config = DEFAULT_CONFIG
            self.save_config()
        else:
            with open(self.config_path, "r") as f:
                user_config = json.load(f)
                old_config = self.config.copy()
                # Merge default config with user config, preserving user values
                self.config = DEFAULT_CONFIG.copy()
                self.config.update(user_config)

                # Log differences if config existed before
                if old_config:
                    differences = list(diff(old_config, self.config))
                    if differences:
                        logging.info("Config changes detected:")
                        for difference in differences:
                            logging.info(f"  {difference}")

                # Save if there were any missing keys
                if len(self.config) > len(user_config):
                    self.save_config()

    def save_config(self) -> None:
        """Save current config to file"""
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=4)

    def get(self, key: str) -> Any:
        """Get config value"""
        return self.config.get(key)

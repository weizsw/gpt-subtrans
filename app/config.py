import json
import os
from typing import Any, Dict

DEFAULT_CONFIG = {
    "server": "http://localhost:8000",
    "endpoint": "/v1/chat/completions",
    "model": "gpt-3.5-turbo",
    "apikey": "",
    "target_language": "en",
    "chat": True,
    "systemmessages": True,
}


class Config:
    def __init__(self, config_path: str = "/app/config/config.json"):
        self.config_path = config_path
        self.config: Dict[Any, Any] = {}
        self.load_config()

    def load_config(self) -> None:
        """Load config from file or create default if not exists"""
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            self.config = DEFAULT_CONFIG
            self.save_config()
        else:
            with open(self.config_path, "r") as f:
                self.config = json.load(f)

    def save_config(self) -> None:
        """Save current config to file"""
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=4)

    def get(self, key: str) -> Any:
        """Get config value"""
        return self.config.get(key)

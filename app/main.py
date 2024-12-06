import json
import logging
import os
import sys
import time
from argparse import Namespace
from typing import Any, Dict

import redis

# Add the parent directory to the sys path so that modules can be found
base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_path)

from config import Config

from PySubtitle.Options import Options
from PySubtitle.SubtitleProject import SubtitleProject
from PySubtitle.SubtitleTranslator import SubtitleTranslator
from scripts.subtrans_common import CreateOptions, CreateProject, CreateTranslator

logger = logging.getLogger("llm-subtrans-service")
logging.basicConfig(level=logging.INFO)


class TranslationService:
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        config_path: str = "/app/config/config.json",
    ):
        self.redis_client = redis.from_url(redis_url)
        self.config = Config(config_path)
        self.queue_name = "translate_queue"

    def create_translation_args(self, file_path: str, output_path: str) -> Namespace:
        """Create translation arguments from config and file path"""
        return Namespace(
            input=file_path,
            output=output_path,
            server=self.config.get("server"),
            endpoint=self.config.get("endpoint"),
            apikey=self.config.get("apikey"),
            model=self.config.get("model"),
            chat=self.config.get("chat"),
            systemmessages=self.config.get("systemmessages"),
            target_language=self.config.get("target_language"),
            projectfile=None,
            write_project=False,
            read_project=False,
            language=None,
            source_language=None,
            includeoriginal=False,
            description=None,
            temperature=0.0,
        )

    def process_message(self, message: Dict[str, Any]) -> None:
        """Process a single translation message"""
        file_path = message.get("input_path")
        output_path = message.get("output_path")

        if not file_path or not os.path.exists(file_path):
            logger.error(f"Invalid input file path: {file_path}")
            return

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as e:
                logger.error(
                    f"Failed to create output directory {output_dir}: {str(e)}"
                )
                return

        try:
            args = self.create_translation_args(file_path, output_path)
            options: Options = CreateOptions(
                args,
                "Local Server",
                api_key=self.config.get("apikey"),
                endpoint=self.config.get("endpoint"),
                model=self.config.get("model"),
                server_address=self.config.get("server"),
                supports_conversation=self.config.get("chat"),
                supports_system_messages=self.config.get("systemmessages"),
            )

            translator: SubtitleTranslator = CreateTranslator(options)
            project: SubtitleProject = CreateProject(options, args)

            result = project.TranslateSubtitles(translator)
            logger.info(
                f"Translation completed for {file_path} -> {output_path}: {result}"
            )

        except Exception as e:
            logger.error(f"Translation failed for {file_path}: {str(e)}")

    def run(self) -> None:
        """Main service loop"""
        logger.info("Translation service started")
        logger.info(f"Using config from: {self.config.config_path}")
        logger.info(f"Redis queue: {self.queue_name}")

        while True:
            try:
                # BLPOP blocks until a message is available
                message = self.redis_client.blpop(self.queue_name, timeout=1)
                if message:
                    _, message_data = message
                    try:
                        message_dict = json.loads(message_data)
                        logger.info("Received translation request:")
                        logger.info(f"  Input: {message_dict.get('input_path')}")
                        logger.info(f"  Output: {message_dict.get('output_path')}")
                        self.process_message(message_dict)
                    except json.JSONDecodeError:
                        logger.error(f"Invalid message format: {message_data}")
                time.sleep(0.1)  # Small delay to prevent CPU spinning
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                time.sleep(1)  # Longer delay on error


if __name__ == "__main__":
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    config_path = os.getenv("CONFIG_PATH", "/app/config/config.json")

    service = TranslationService(redis_url, config_path)
    service.run()

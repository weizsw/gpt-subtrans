import json
import logging
import os
import sys
import time
from argparse import Namespace
from typing import Any, Dict

import redis
import requests

# Add the parent directory to the sys path so that modules can be found
base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_path)

# Add the current directory to the path for local imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

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
        config_path: str = None,
    ):
        self.config = Config(config_path)
        self.redis_client = redis.from_url(self.config.get("redis_url"))
        self.queue_name = "translate_queue"

    def create_translation_args(self, file_path: str) -> Namespace:
        """Create translation arguments from config and file path"""
        return Namespace(
            # Core translation settings
            filepath=file_path,
            server=self.config.get("server"),
            endpoint=self.config.get("endpoint"),
            apikey=self.config.get("apikey"),
            model=self.config.get("model"),
            chat=self.config.get("chat"),
            systemmessages=self.config.get("systemmessages"),
            target_language=self.config.get("target_language"),
            # Project settings
            projectfile=None,
            write_project=False,
            read_project=False,
            # Language settings
            language=None,
            source_language=None,
            includeoriginal=None,
            # Translation behavior
            description=None,
            temperature=None,
            # Batch processing settings
            maxbatchsize=None,
            minbatchsize=None,
            scenethreshold=None,
            # Processing options
            preprocess=None,
            postprocess=None,
            # Rate limiting
            ratelimit=None,
            # Error handling
            retry_on_error=True,
            stop_on_error=False,
            # Debug options
            debug=False,
            # Additional options
            addrtlmarkers=None,
            instruction=None,
            instructionfile=None,
            matchpartialwords=None,
            maxsummaries=None,
            maxlines=None,
            moviename=None,
            names=None,
            name=None,
            project=None,
            substitution=None,
            writebackup=None,
            output=None,
            input=file_path,  # Required by some functions that reference args.input
        )

    def send_callback(self, callback_url: str, file_path: str) -> None:
        """Send callback request with subtitle paths"""
        try:
            # Generate Chinese subtitle path by adding 'zh' before the last extension
            base, ext = os.path.splitext(file_path)
            if base.endswith(".eng"):
                base = base[:-4]  # Remove '.eng'
            chs_subtitle_path = f"{base}.eng.zh{ext}"

            payload = {
                "chs_subtitle_path": chs_subtitle_path,
                "eng_subtitle_path": file_path,
            }

            response = requests.post(callback_url, json=payload)
            response.raise_for_status()
            logger.info(f"Callback sent successfully to {callback_url}")

        except Exception as e:
            logger.error(f"Failed to send callback: {str(e)}")

    def process_message(self, message: Dict[str, Any]) -> None:
        """Process a single translation message"""
        file_path = message.get("path")
        overview = message.get("overview")

        if not file_path or not os.path.exists(file_path):
            logger.error(f"File not found at path: {file_path}")
            return

        try:
            args = self.create_translation_args(file_path)
            options: Options = CreateOptions(
                args,
                "Local Server",
                api_key=self.config.get("apikey"),
                endpoint=self.config.get("endpoint"),
                model=self.config.get("model"),
                server_address=self.config.get("server"),
                target_language=self.config.get("target_language"),
                supports_conversation=self.config.get("chat"),
                supports_system_messages=self.config.get("systemmessages"),
                description=overview,
            )

            translator: SubtitleTranslator = CreateTranslator(options)
            project: SubtitleProject = CreateProject(options, args)

            project.TranslateSubtitles(translator)
            logger.info(f"Translation completed for {file_path}: success")

        except Exception as e:
            logger.error(f"Translation failed for {file_path}: {str(e)}")

    def run(self) -> None:
        """Main service loop"""
        logger.info("Translation service started")
        logger.info(f"Using config from: {self.config.config_path}")
        logger.info(f"Redis queue: {self.queue_name}")

        # Add config value logging
        logger.info("Configuration:")
        for key, value in self.config.config.items():
            # Mask API key for security
            if key == "apikey":
                masked_value = value[:8] + "..." + value[-4:] if value else None
                logger.info(f"  {key}: {masked_value}")
            else:
                logger.info(f"  {key}: {value}")

        while True:
            try:
                # BLPOP blocks until a message is available
                message = self.redis_client.blpop(self.queue_name, timeout=1)
                if message:
                    _, message_data = message
                    try:
                        message_dict = json.loads(message_data)
                        logger.info("Received translation request:")
                        logger.info(f"  Path: {message_dict.get('path')}")
                        logger.info(f"  Name: {message_dict.get('file_name')}")
                        logger.info(f"  Overview: {message_dict.get('overview')}")
                        self.process_message(message_dict)

                        callback_url = self.config.get("callback_url")
                        if callback_url:
                            self.send_callback(callback_url, message_dict.get("path"))

                    except json.JSONDecodeError:
                        logger.error(f"Invalid message format: {message_data}")
                    except Exception as e:
                        logger.error(f"Error processing message: {str(e)}")
                        # Don't send callback on error

                time.sleep(0.1)  # Small delay to prevent CPU spinning
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                time.sleep(1)  # Longer delay on error


if __name__ == "__main__":
    # Get config path from environment or use default based on environment
    config_path = os.getenv("CONFIG_PATH")
    if not config_path:
        if os.environ.get("DOCKER_ENV") == "true":
            config_path = "/app/configs/config.json"
        else:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "configs",
                "config.json",
            )

    # Initialize and run service
    service = TranslationService(config_path)
    service.run()

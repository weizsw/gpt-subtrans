import importlib.util
import logging

if not importlib.util.find_spec("anthropic"):
    logging.info("Anthropic SDK is not installed. Claude provider will not be available")
else:
    try:
        import anthropic
        import os

        from copy import deepcopy

        from PySubtitle.Helpers import GetEnvFloat, GetEnvInteger
        from PySubtitle.Helpers.Parse import ParseNames
        from PySubtitle.Providers.Anthropic.AnthropicClient import AnthropicClient
        from PySubtitle.TranslationClient import TranslationClient
        from PySubtitle.TranslationProvider import TranslationProvider

        class Provider_Claude(TranslationProvider):
            name = "Claude"

            information = """
            <p>Select the <a href="https://docs.anthropic.com/claude/docs/models-overview">AI model</a> to use as a translator.</p>
            <p>Note that each model has a <a href="https://docs.anthropic.com/claude/docs/models-overview">maximum tokens limit</a>.</p>
            <p>See the <a href="https://docs.anthropic.com/claude/reference/rate-limits">Anthropic documentation</a> for information on rate limits and costs</p>
            """

            information_noapikey = """
            <p>To use Claude you need to provide an <a href="https://console.anthropic.com/settings/keys">Anthropic API Key </a>.</p>
            """

            default_models = ['claude-3-5-haiku-latest', 'claude-3-5-sonnet-latest', 'claude-3-opus-latest', 'claude-3-haiku-20240307', 'claude-3-sonnet-20240229', 'claude-3-5-sonnet-20240620']

            def __init__(self, settings : dict):
                super().__init__(self.name, {
                    "api_key": settings.get('api_key') or os.getenv('CLAUDE_API_KEY'),
                    "model": settings.get('model') or os.getenv('CLAUDE_MODEL'),
                    "max_tokens": settings.get('max_tokens') or GetEnvInteger('CLAUDE_MAX_TOKENS', 4096),
                    'temperature': settings.get('temperature', GetEnvFloat('CLAUDE_TEMPERATURE', 0.0)),
                    'rate_limit': settings.get('rate_limit', GetEnvFloat('CLAUDE_RATE_LIMIT', 10.0)),
                    'custom_models': settings.get('custom_models') or os.getenv('CLAUDE_CUSTOM_MODELS'),
                    'proxy': settings.get('proxy') or os.getenv('CLAUDE_PROXY'),
                })

                self.refresh_when_changed = ['api_key', 'model', 'custom_models']

            @property
            def api_key(self):
                return self.settings.get('api_key')

            def GetTranslationClient(self, settings : dict) -> TranslationClient:
                client_settings : dict = deepcopy(self.settings)
                client_settings.update(settings)
                client_settings.update({
                    'supports_conversation': True,
                    'supports_system_messages': False,
                    'supports_system_prompt': True
                    })
                return AnthropicClient(client_settings)

            def GetAvailableModels(self) -> list[str]:
                if not self.api_key:
                    return []

                # TODO: surely the SDK has a method for this?
                # client = anthropic.Anthropic(api_key=self.api_key)
                # models = client.list_models()
                custom_models = ParseNames(self.settings.get('custom_models'))
                models = sorted(set(custom_models + self.default_models))

                return models

            def RefreshAvailableModels(self):
                self._available_models = self.GetAvailableModels()

            def GetInformation(self):
                return self.information if self.api_key else self.information_noapikey

            def GetOptions(self) -> dict:
                options = {'api_key': (str, "An Anthropic Claude API key is required to use this provider (https://console.anthropic.com/settings/keys)")}

                if not self.api_key:
                    return options

                self.RefreshAvailableModels()

                options['custom_models'] = (str, "Comma separated list of additional Claude models (until Anthropic provide a method to retrieve them)")

                if self.available_models:
                    options.update({
                        'model': (self.available_models, "The model to use for translations"),
                        'temperature': (float, "The temperature to use for translations (default 0.0)"),
                        'rate_limit': (float, "The rate limit to use for translations (default 60.0)"),
                        'max_tokens': (int, "The maximum number of tokens to use for translations"),
                        'proxy': (str, "Optional proxy server to use for requests (e.g. https://api.not-anthropic.com/"),
                    })

                return options

            def _allow_multithreaded_translation(self) -> bool:
                """
                If user has set a rate limit don't attempt parallel requests to make sure we respect it
                """
                if self.settings.get('rate_limit', 0.0) != 0.0:
                    return False

                return True

    except ImportError:
        logging.info("Anthropic SDK not installed. Claude provider will not be available")

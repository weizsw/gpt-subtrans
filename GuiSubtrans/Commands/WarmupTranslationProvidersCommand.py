import logging
from typing import cast

from GuiSubtrans.Command import Command
from PySubtrans.Options import Options
from PySubtrans.TranslationProvider import TranslationProvider


class WarmupTranslationProvidersCommand(Command):
    """Warm the active and previously configured provider libraries in the background."""

    def __init__(self, options : Options, active_provider : TranslationProvider|None = None):
        super().__init__()
        self.is_blocking = False
        self.skip_undo = True
        self.can_undo = False
        self.mark_project_dirty = False
        self.updates_datamodel = False
        self.warmed_providers : dict[str, TranslationProvider] = {}
        self.options = Options(options)
        self.active_provider = active_provider

    def execute(self) -> bool:
        """Warm configured providers as a best-effort background task."""
        warmed_providers = 0
        available_providers = TranslationProvider.get_providers()
        logging.info("Starting background translation-provider warm-up")

        active_provider_name = self.active_provider.name if self.active_provider else self.options.provider
        configured_provider_names = set(self.options.provider_settings)
        provider_names = [
            provider_name for provider_name in available_providers
            if provider_name in configured_provider_names or provider_name == active_provider_name
        ]
        if active_provider_name in provider_names:
            provider_names.remove(active_provider_name)
            provider_names.insert(0, active_provider_name)

        for provider_name in provider_names:
            if self.aborted:
                logging.info("Aborted background translation-provider warm-up")
                return True

            try:
                provider_type = available_providers[provider_name]
                provider_class = cast(type[TranslationProvider], provider_type)
                provider_class.WarmUp()

                if self.aborted:
                    logging.info("Aborted background translation-provider warm-up")
                    return True

                if provider_name == active_provider_name and self.active_provider:
                    provider = self.active_provider
                else:
                    provider_settings = self.options.GetProviderSettings(provider_name)
                    provider = TranslationProvider.create_provider(provider_name, provider_settings)

                self.warmed_providers[provider_name] = provider
                warmed_providers += 1
                logging.debug("Background-warmed provider: %s", provider_name)
            except Exception as error:
                logging.warning("Unable to warm %s provider: %s", provider_name, error)

            if self.aborted:
                logging.info("Aborted background translation-provider warm-up")
                return True

        logging.info("Completed background translation-provider warm-up (%d providers)", warmed_providers)
        return True

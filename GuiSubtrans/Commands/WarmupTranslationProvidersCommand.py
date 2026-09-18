import logging
from typing import cast

from GuiSubtrans.Command import Command
from PySubtrans.TranslationProvider import TranslationProvider


class WarmupTranslationProvidersCommand(Command):
    """Warm deferred provider dependencies without changing application state."""

    def __init__(self):
        super().__init__()
        self.is_blocking = False
        self.skip_undo = True
        self.can_undo = False
        self.mark_project_dirty = False
        self.updates_datamodel = False

    def execute(self) -> bool:
        """Warm every registered provider as a best-effort background task."""
        warmed_providers = 0
        available_providers = TranslationProvider.get_providers()
        logging.info("Starting background translation-provider warm-up")

        for provider_name, provider_type in available_providers.items():
            if self.aborted:
                logging.info("Aborted background translation-provider warm-up")
                return True

            try:
                provider_class = cast(type[TranslationProvider], provider_type)
                provider_class.WarmUp()
                warmed_providers += 1
                logging.debug("Background-warmed provider: %s", provider_name)
            except Exception as error:
                logging.warning("Unable to warm %s provider: %s", provider_name, error)

            if self.aborted:
                logging.info("Aborted background translation-provider warm-up")
                return True

        logging.info("Completed background translation-provider warm-up (%d providers)", warmed_providers)
        return True

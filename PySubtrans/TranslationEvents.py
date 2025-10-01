import logging
from blinker import Signal


class TranslationEvents:
    """
    Container for blinker signals emitted during translation.

    Subscribe to events to receive notifications from long-running translation tasks,
    e.g. to provide progress feedback or UI updates.

    Signals:
        batch_translated(sender, batch): 
            Emitted after each batch is translated

        batch_updated(sender, batch): 
            Emitted after each batch is updated in the subtitle project

        scene_translated(sender, scene): 
            Emitted when a complete scene has been translated

        preprocessed(sender, scenes): 
            Emitted after subtitles are batched and pre-processed (GuiSubtrans only)

        error(sender, message): 
            Signals that an error was encountered during translation

        warning(sender, message): 
            Signals that a warning was encountered during translation

        info(sender, message): 
            General informational message during translation
    """
    preprocessed: Signal
    batch_translated: Signal
    batch_updated: Signal
    scene_translated: Signal
    error: Signal
    warning: Signal
    info: Signal

    def __init__(self):
        self.preprocessed = Signal("translation-preprocessed")
        self.batch_translated = Signal("translation-batch-translated")
        self.batch_updated = Signal("translation-batch-updated")
        self.scene_translated = Signal("translation-scene-translated")

        # Signals for logging translation events
        self.error = Signal("translation-error")
        self.warning = Signal("translation-warning")
        self.info = Signal("translation-info")

    def connect_default_loggers(self):
        """
        Connect default logging handlers to logging signals.
        """
        self.error.connect(logging.error)
        self.warning.connect(logging.warning)
        self.info.connect(logging.info)

    def disconnect_default_loggers(self):
        """
        Disconnect default logging handlers from the signals.
        """
        self.error.disconnect(logging.error)
        self.warning.disconnect(logging.warning)
        self.info.disconnect(logging.info)

    def connect_logger(self, logger : logging.Logger):
        """
        Connect a custom logger to the logging signals.

        Args:
            logger: The logger instance to connect to error, warning, and info signals
        """
        # Create wrapper functions to adapt signal kwargs to logger positional args
        def error_wrapper(sender, message):
            logger.error(message)

        def warning_wrapper(sender, message):
            logger.warning(message)

        def info_wrapper(sender, message):
            logger.info(message)

        self.error.connect(error_wrapper)
        self.warning.connect(warning_wrapper)
        self.info.connect(info_wrapper)

from PySide6.QtCore import QObject, QThread, Signal, Slot

from PySubtrans.TranslationProvider import TranslationProvider

# Keep running loaders referenced until their thread finishes, so a superseded
# loader cannot be garbage collected while its worker is still running.
_active_loaders : set['TranslationProviderModelLoader'] = set()


class TranslationProviderModelLoader(QObject):
    """
    Resolves a translation provider's model list off the GUI thread.

    Owns the worker thread and its lifetime.
    The provider's name is reported back so late results for a superseded provider can be ignored.
    A failed load reports the persisted model, keeping it selectable.
    """
    loaded = Signal(str, list)
    failed = Signal(str, str)

    def __init__(self, provider : TranslationProvider, owner : QObject|None = None):
        # The loader must have no parent for moveToThread to work; owner only parents the thread.
        super().__init__()
        self.provider = provider
        self._owner = owner
        self._thread : QThread|None = None
        self._abandoned : bool = False

    @property
    def running(self) -> bool:
        """Whether the load is still in progress."""
        return self._thread is not None and self._thread.isRunning()

    def start(self) -> None:
        """Run the load on a worker thread."""
        if self._thread is not None:
            return

        self.provider.model_list.Request()

        thread = QThread(self._owner)
        self.moveToThread(thread)
        thread.started.connect(self.run)
        self.loaded.connect(thread.quit)
        self.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)

        _active_loaders.add(self)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Release the loader without blocking the GUI thread.
        The worker finishes on its own and its result is ignored, since a request cannot be interrupted.
        """
        self._abandoned = True
        thread = self._thread
        if thread is not None:
            thread.quit()

    @Slot()
    def run(self) -> None:
        """Resolve the model list, emitting the result back to the caller."""
        provider = self.provider

        try:
            models = provider.GetAvailableModels()
            if self._abandoned:
                return
            provider.model_list.Store(models)
            self.loaded.emit(provider.name, models)
        except Exception as e:
            if self._abandoned:
                return
            persisted_model = provider.selected_model
            provider.model_list.Store([persisted_model] if persisted_model else [], resolved=False)
            self.failed.emit(provider.name, str(e))

    @Slot()
    def _on_thread_finished(self) -> None:
        """Release the thread and loader once the worker has stopped."""
        thread = self._thread
        self._thread = None
        _active_loaders.discard(self)
        if thread is not None:
            thread.deleteLater()
        self.deleteLater()

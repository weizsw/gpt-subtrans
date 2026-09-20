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
    The outcome is recorded on the provider's ModelList, so a failure keeps the persisted model selectable.
    """
    loaded = Signal(str)
    failed = Signal(str, str)

    def __init__(self, provider : TranslationProvider, owner : QObject|None = None):
        # The loader must have no parent for moveToThread to work; owner only parents the thread.
        super().__init__()
        self.provider = provider
        self._owner = owner
        self._thread : QThread|None = None
        self._request : int = 0

    @property
    def running(self) -> bool:
        """Whether the load is still in progress."""
        return self._thread is not None and self._thread.isRunning()

    def start(self) -> None:
        """Run the load on a worker thread."""
        if self._thread is not None:
            return

        self._request = self.provider.model_list.BeginLoad()

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
        The worker finishes on its own and its result is discarded, since a request cannot be interrupted.
        """
        # Cancelling makes the request stale, so the worker cannot record its result
        self.provider.model_list.Cancel()

        thread = self._thread
        if thread is not None:
            thread.quit()

    @Slot()
    def run(self) -> None:
        """Resolve the model list on the provider, emitting the outcome back to the caller."""
        provider = self.provider

        if not provider.model_list.Resolve(self._request):
            # A superseded or cancelled lookup has nothing to report
            return

        if provider.model_list.resolved:
            self.loaded.emit(provider.name)
        else:
            self.failed.emit(provider.name, provider.model_list.error or "")

    @Slot()
    def _on_thread_finished(self) -> None:
        """Release the thread and loader once the worker has stopped."""
        thread = self._thread
        self._thread = None
        _active_loaders.discard(self)
        if thread is not None:
            thread.deleteLater()
        self.deleteLater()

from collections.abc import Callable


class ModelList:
    """
    Lazily resolved list of models for a translation provider.

    Holds the known models and tracks whether a lookup has completed.
    An async load can be requested so the list stops blocking callers while it resolves elsewhere.
    The known models are returned until then.
    """
    def __init__(self, fetch : Callable[[], list[str]]):
        self._fetch = fetch
        self._models : list[str] = []
        self._resolved : bool = False
        self._pending : bool = False

    @property
    def models(self) -> list[str]:
        """
        The known models, fetching them when no load has been requested
        """
        if self._pending:
            return self._models

        if not self._resolved:
            self.Store(self._fetch())

        return self._models

    @property
    def resolved(self) -> bool:
        """
        Whether the model list has been resolved, even if it is empty
        """
        return self._resolved

    @property
    def known(self) -> list[str]:
        """
        The known models, never triggering a lookup
        """
        return self._models

    @property
    def pending(self) -> bool:
        """
        Whether an asynchronous load has been requested
        """
        return self._pending

    def Store(self, models : list[str], resolved : bool = True) -> None:
        """
        Record the model list and complete any pending load
        """
        self._models = list(models)
        self._resolved = resolved
        self._pending = False

    def Request(self) -> None:
        """
        Stop blocking callers until Store completes the load
        """
        self._pending = True

    def Reset(self) -> None:
        """
        Discard the known models and any pending load
        """
        self._models = []
        self._resolved = False
        self._pending = False

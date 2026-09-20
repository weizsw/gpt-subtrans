from collections.abc import Callable
from enum import Enum, auto


class ModelListState(Enum):
    """The lifecycle of a provider's model list."""
    Unloaded = auto()   # No lookup has been attempted
    Loading = auto()    # An asynchronous lookup is in progress
    Loaded = auto()     # The list is authoritative, even if it is empty
    Failed = auto()     # The last lookup failed and may be retried


class ModelList:
    """
    The model list for a translation provider, modelled as a state machine.

    Holds the known models and tracks the lifecycle of a lookup.
    A failed lookup is recorded as state rather than raised to callers.
    """
    def __init__(self, fetch : Callable[[], list[str]]):
        self._fetch = fetch
        self._models : list[str] = []
        self._state : ModelListState = ModelListState.Unloaded
        self._error : str|None = None

    @property
    def state(self) -> ModelListState:
        """
        The current lifecycle state
        """
        return self._state

    @property
    def models(self) -> list[str]:
        """
        The known models, resolving them when there is no resolved or in-progress list
        """
        if self._state in (ModelListState.Unloaded, ModelListState.Failed):
            self.Resolve()

        return self._models

    @property
    def known(self) -> list[str]:
        """
        The known models, never triggering a lookup
        """
        return self._models

    @property
    def resolved(self) -> bool:
        """
        Whether the list is authoritative, even if it is empty
        """
        return self._state == ModelListState.Loaded

    @property
    def pending(self) -> bool:
        """
        Whether an asynchronous lookup is in progress
        """
        return self._state == ModelListState.Loading

    @property
    def error(self) -> str|None:
        """
        The error from the last failed lookup
        """
        return self._error

    def BeginLoad(self) -> None:
        """
        Mark an asynchronous lookup as in progress
        """
        if self._state != ModelListState.Loading:
            self._state = ModelListState.Loading

    def Resolve(self) -> None:
        """
        Run the lookup and record the outcome as state. Never raises.
        """
        self._state = ModelListState.Loading
        try:
            self._models = list(self._fetch())
            self._error = None
            self._state = ModelListState.Loaded
        except Exception as error:
            self._error = str(error)
            self._state = ModelListState.Failed

    def Cancel(self) -> None:
        """
        Abandon an in-progress lookup so it can be retried
        """
        if self._state == ModelListState.Loading:
            self._state = ModelListState.Unloaded

    def Reset(self) -> None:
        """
        Discard the known models and any lifecycle state
        """
        self._models = []
        self._state = ModelListState.Unloaded
        self._error = None

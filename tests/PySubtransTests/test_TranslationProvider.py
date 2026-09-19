from PySubtrans.Helpers.TestCases import DummyProvider, LoggedTestCase
from PySubtrans.Helpers.Tests import log_input_expected_error, skip_if_debugger_attached
from PySubtrans.Options import Options
from PySubtrans.SettingsType import SettingsType
from PySubtrans.TranslationProvider import TranslationProvider


class CountingProvider(TranslationProvider):
    """Provider that counts model lookups so caching can be observed."""
    name = "Counting Provider"

    def __init__(self):
        super().__init__(self.name, SettingsType({'model': 'model-a'}))
        self.lookup_count = 0

    def GetAvailableModels(self) -> list[str]:
        self.lookup_count += 1
        return ['model-a', 'model-b']


class TranslationProviderTests(LoggedTestCase):

    def test_create_provider_case_insensitive(self):
        """create_provider accepts a non-canonical provider name casing"""
        provider = TranslationProvider.create_provider("dummy provider", SettingsType())
        self.assertLoggedIsInstance("case-insensitive lookup returns DummyProvider", provider, DummyProvider)

    @skip_if_debugger_attached
    def test_create_provider_unknown_raises(self):
        """create_provider raises ValueError for an unregistered provider name"""
        with self.assertRaises(ValueError) as context:
            TranslationProvider.create_provider("no such provider", SettingsType())
        log_input_expected_error("no such provider", ValueError, context.exception)

    def test_get_provider_normalizes_name(self):
        """get_provider normalizes options.provider to the canonical registered name and applies settings"""
        options = Options({
            'provider': 'dummy provider',
            'provider_settings': {
                'Dummy Provider': SettingsType({'model': 'test-model-xyz'})
            },
            'target_language': 'French',
        })

        provider = TranslationProvider.get_provider(options)

        self.assertLoggedIsInstance("get_provider returns DummyProvider", provider, DummyProvider)
        self.assertLoggedEqual("options.provider normalized to canonical name", "Dummy Provider", options.provider)
        self.assertLoggedEqual("provider received settings from options", "test-model-xyz", provider.settings.get_str('model'))

    def test_available_models_are_cached_and_resettable(self):
        """Model lookups are cached until the list is reset."""
        provider = CountingProvider()

        self.assertLoggedFalse("models not loaded initially", provider.model_list.resolved)
        self.assertLoggedEqual("first lookup returns models", ['model-a', 'model-b'], provider.available_models)
        self.assertLoggedEqual("lookup count after first access", 1, provider.lookup_count)
        self.assertLoggedTrue("models marked loaded", provider.model_list.resolved)

        self.assertLoggedEqual("second lookup returns cached models", ['model-a', 'model-b'], provider.available_models)
        self.assertLoggedEqual("lookup count unchanged", 1, provider.lookup_count)

        provider.ResetAvailableModels()
        self.assertLoggedFalse("reset clears loaded state", provider.model_list.resolved)
        self.assertLoggedEqual("lookup after reset refetches", ['model-a', 'model-b'], provider.available_models)
        self.assertLoggedEqual("lookup count after reset", 2, provider.lookup_count)

    def test_set_available_models_marks_loaded(self):
        """An explicitly set list, even empty, is not looked up again."""
        provider = CountingProvider()

        provider.model_list.Store([])

        self.assertLoggedTrue("empty list is loaded", provider.model_list.resolved)
        self.assertLoggedEqual("empty list returned", [], provider.available_models)
        self.assertLoggedEqual("no lookup performed", 0, provider.lookup_count)

    def test_available_models_can_be_fetched_eagerly(self):
        """Without an async request the provider fulfils a direct model request itself."""
        provider = CountingProvider()

        models = provider.available_models

        self.assertLoggedEqual("models fetched on demand", ['model-a', 'model-b'], models)
        self.assertLoggedEqual("lookup performed", 1, provider.lookup_count)
        self.assertLoggedFalse("no async request recorded", provider.model_list.pending)

    def test_requested_load_defers_to_async_population(self):
        """Once an async load is requested the provider stops fetching on its own."""
        provider = CountingProvider()

        provider.model_list.Request()
        models = provider.available_models

        self.assertLoggedTrue("async load requested", provider.model_list.pending)
        self.assertLoggedEqual("no models returned while loading", [], models)
        self.assertLoggedEqual("provider did not fetch", 0, provider.lookup_count)
        self.assertLoggedFalse("not marked loaded", provider.model_list.resolved)

    def test_set_available_models_completes_requested_load(self):
        """Completing a requested load clears the request and marks the list resolved."""
        provider = CountingProvider()
        provider.model_list.Request()

        provider.model_list.Store(['model-a', 'model-b', 'model-c'])

        self.assertLoggedFalse("request cleared", provider.model_list.pending)
        self.assertLoggedTrue("models marked loaded", provider.model_list.resolved)
        self.assertLoggedEqual("models available", ['model-a', 'model-b', 'model-c'], provider.available_models)
        self.assertLoggedEqual("provider did not fetch", 0, provider.lookup_count)

    def test_load_in_flight_does_not_block_eager_access(self):
        """An in-flight async load returns the known models without blocking, and a setter completes it."""
        provider = CountingProvider()
        provider.model_list.Store(['persisted-model'])
        provider.model_list.Request()

        # e.g. ProjectDataModel.available_models while the dialog is still loading
        self.assertLoggedEqual("known models returned while loading", ['persisted-model'], provider.available_models)
        self.assertLoggedEqual("provider did not block with a fetch", 0, provider.lookup_count)

        # The loader then completes the request
        provider.model_list.Store(['model-a', 'model-b'])
        self.assertLoggedEqual("resolved models returned after load", ['model-a', 'model-b'], provider.available_models)
        self.assertLoggedTrue("models marked loaded", provider.model_list.resolved)

    def test_failed_requested_load_allows_retry(self):
        """A failed async load keeps the persisted model but stays unresolved for a retry."""
        provider = CountingProvider()
        provider.model_list.Request()

        provider.model_list.Store(['model-a'], resolved=False)

        self.assertLoggedFalse("request completed", provider.model_list.pending)
        self.assertLoggedFalse("not marked loaded", provider.model_list.resolved)
        self.assertLoggedEqual("known model retained", ['model-a'], provider.model_list.known)

        # A later eager access can still fetch
        self.assertLoggedEqual("retry fetches models", ['model-a', 'model-b'], provider.available_models)
        self.assertLoggedEqual("lookup performed on retry", 1, provider.lookup_count)

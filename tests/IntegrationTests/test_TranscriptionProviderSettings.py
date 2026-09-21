"""Provider settings that change the displayed information must be declared as refresh triggers.

A language hint is validated in the provider information, so changing it has to refresh the
form - declared on the provider rather than special-cased by key in the dialog.

Concrete providers cannot be imported by the unit suites (see tests/ProviderImportGuard.py),
so these checks live in the integration suite.
"""
from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.SettingsType import SettingsType
from PySubtrans.Transcription.TranscriptionProvider import TranscriptionProvider


class TestTranscriptionProviderRefreshTriggers(LoggedTestCase):
    """Every provider offering a language hint declares it as a refresh trigger."""

    def test_language_is_declared_where_it_is_offered(self) -> None:
        for name, provider_class in sorted(TranscriptionProvider.get_providers().items()):
            provider = provider_class(SettingsType())

            if 'language' not in provider.settings:
                continue

            self.assertLoggedIn(f'{name} refreshes on language', 'language', provider.refresh_when_changed,
                                input_value=provider.refresh_when_changed)

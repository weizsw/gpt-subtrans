"""Checks on the settings every concrete transcription provider declares.

A language hint is validated in the provider information, so changing it has to refresh the
form - declared on the provider rather than special-cased by key in the dialog.

Concrete providers cannot be imported by the unit suites (see tests/ProviderImportGuard.py),
so these checks live in the integration suite.
"""
import os
from unittest.mock import patch

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


class TestTranscriptionProviderUnsetSettings(LoggedTestCase):
    """A setting with no default is still a known key, so a later update applies to it."""

    def test_unset_rate_limit_can_be_updated(self) -> None:
        # Every provider's rate limit defaults to its environment variable, which is None when unset
        unset_environment = {key: value for key, value in os.environ.items() if not key.endswith('RATE_LIMIT')}

        with patch.dict(os.environ, unset_environment, clear=True):
            for name, provider_class in sorted(TranscriptionProvider.get_providers().items()):
                provider = provider_class(SettingsType())
                provider.UpdateSettings(SettingsType({'rate_limit': 12.0}))

                self.assertLoggedEqual(f'{name} rate limit', 12.0, provider.settings.get_float('rate_limit'))

import os

from PySubtrans.Helpers.Localization import _
from PySubtrans.Options import env_float
from PySubtrans.SettingsType import GuiSettingsType, SettingsType
from PySubtrans.Transcription.TranscriptionClient import TranscriptionClient
from PySubtrans.Transcription.TranscriptionLines import (DEFAULT_MERGE_ELIGIBLE_GAP_SECONDS,
                                                         DEFAULT_SAME_SPEAKER_MERGE_ELIGIBLE_GAP_SECONDS)
from PySubtrans.Transcription.TranscriptionProvider import OptionsScope, TranscriptionProvider


class MuseTranscriptionProvider(TranscriptionProvider):
    """
    Speech-to-text via Meta Muse Voice Transcribe with the Model API key.

    Turn-level timings with speaker labels in DIARIZATION mode; the plain
    PUSH_TO_TALK mode returns no turn timings and cannot produce subtitles.
    """
    name = "Muse"

    information = _("""
    <p>Transcribe with Meta Muse Voice Transcribe.</p>
    <p>Note that Muse does not provide word-level timings, so subtitle timing is "best effort".</p>
    """)

    information_noapikey = _("""
    <p>To use this provider you need a Meta <a href="https://dev.meta.ai/">Model API key</a>.</p>
    """)

    @property
    def recommended_min_chunk_seconds(self) -> float:
        """Short chunks bound request bodies and the blast radius of retries."""
        return 8.0

    @property
    def recommended_max_chunk_seconds(self) -> float:
        """Short chunks bound request bodies and the blast radius of retries."""
        return 60.0

    @property
    def supports_diarization(self) -> bool:
        """Speaker labels are kept only when diarization is opted into."""
        return self.settings.get_bool('diarize', False)

    def __init__(self, settings : SettingsType):
        super().__init__(self.name, SettingsType({
            'api_key': settings.get_str('api_key', os.getenv('MUSE_API_KEY', os.getenv('MODEL_API_KEY'))),
            'server_address': settings.get_str('server_address', os.getenv('MUSE_SERVER_ADDRESS', 'https://api.meta.ai/v1')),
            'model': settings.get_str('model', os.getenv('MUSE_STT_MODEL', 'muse-voice-transcribe-1.0')),
            'language': settings.get_str('language', os.getenv('TRANSCRIPTION_LANGUAGE')),
            'diarize': settings.get_bool('diarize', False),
            'request_timeout': settings.get_float('request_timeout', env_float('TRANSCRIPTION_TIMEOUT', 300.0)),
            'rate_limit': settings.get_float('rate_limit', env_float('MUSE_TRANSCRIPTION_RATE_LIMIT')),
            'proxy': settings.get_str('proxy') or os.getenv('MUSE_PROXY'),
            'merge_eligible_gap': settings.get_float('merge_eligible_gap', DEFAULT_MERGE_ELIGIBLE_GAP_SECONDS),
            'same_speaker_merge_eligible_gap': settings.get_float(
                'same_speaker_merge_eligible_gap', DEFAULT_SAME_SPEAKER_MERGE_ELIGIBLE_GAP_SECONDS),
            'can_merge_different_speakers': settings.get_bool('can_merge_different_speakers', True),
        }))

        self.refresh_when_changed = ['api_key', 'language', 'diarize']

    def GetAvailableModels(self) -> list[str]:
        """Timed transcription models served by this provider."""
        return ['muse-voice-transcribe-1.0']

    def GetTranscriptionClient(self, settings : SettingsType) -> TranscriptionClient:
        """Returns a new client merging provider defaults with call settings."""
        # Sanctioned lazy import: keeps provider registration light.
        from PySubtrans.Transcription.Providers.Clients.MuseTranscriptionClient import MuseTranscriptionClient
        client_settings = SettingsType(self.settings.copy())
        client_settings.update(settings)
        return MuseTranscriptionClient(client_settings)

    def GetOptions(self, settings : SettingsType, scope : OptionsScope = OptionsScope.ALL) -> GuiSettingsType:
        """
        Returns the configurable options for the provider.
        """
        options : GuiSettingsType = {}

        if scope is OptionsScope.ALL:
            options['api_key'] = (str, _("A Meta Model API key (MODEL_API_KEY)"))

        if not self.settings.get_str('api_key'):
            return options

        options.update({
            'model': (self.available_models, _("Speech-to-text model (turns carry timings)")),
            'language': (str, _("Spoken language hint, e.g. english (optional, auto-detected when empty)")),
            'diarize': (bool, _("Identify speakers (DIARIZATION mode)")),
        })

        if scope is OptionsScope.ALL:
            options['request_timeout'] = (float, _("Per-chunk request timeout in seconds"))
            options['rate_limit'] = (float, _("Maximum API requests per minute (0 for unlimited)"))
            options['merge_eligible_gap'] = (float, _(
                "Widest gap, in seconds, across which transcribed lines can still be merged"))

            if self.supports_diarization:
                options['same_speaker_merge_eligible_gap'] = (float, _(
                    "Widest gap, in seconds, across which lines can be merged when the speaker has not changed"))
                options['can_merge_different_speakers'] = (bool, _(
                    "Allow brief lines by different speakers to be combined into a single line of dialogue"))

        return options

    def ValidateSettings(self) -> bool:
        """Validate the settings for the provider."""
        if not self.settings.get_str('api_key'):
            self.validation_message = _("API Key is required")
            return False

        return True

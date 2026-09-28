"""Command-line options for scripts/transcribe.py (no media or backends needed)."""
import logging
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', 'scripts')))

import transcribe  # type: ignore[import-not-found] - scripts dir added to sys.path above

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Helpers.Tests import skip_if_debugger_attached
from PySubtrans.SubtitleError import SubtitleError


class TestTranscribeCliOptions(LoggedTestCase):
    def _parse(self, *argv : str):
        return transcribe.CreateTranscribeParser().parse_args(list(argv))

    def test_format_defaults_to_vtt(self):
        """Transcribed output defaults to VTT to preserve speakers."""
        args = self._parse("movie.mkv")

        self.assertLoggedEqual("default format", "vtt", args.format)

    def test_format_srt_selected(self):
        """SRT output is selectable (drops speaker labels)."""
        args = self._parse("movie.mkv", "--format", "srt")

        self.assertLoggedEqual("srt format", "srt", args.format)

    def test_format_ass_selected(self):
        """ASS output is selectable."""
        args = self._parse("movie.mkv", "--format", "ass")

        self.assertLoggedEqual("ass format", "ass", args.format)

    def test_format_vtt_selected(self):
        """VTT output is selectable."""
        args = self._parse("movie.mkv", "--format", "vtt")

        self.assertLoggedEqual("vtt format", "vtt", args.format)

    def test_chunk_bounds_default_to_provider(self):
        """Chunk bounds stay unset so provider settings apply."""
        args = self._parse("movie.mkv")

        self.assertLoggedEqual("min unset", None, args.min_chunk)
        self.assertLoggedEqual("max unset", None, args.max_chunk)

    def test_ffmpeg_path_is_optional(self):
        """The CLI keeps PATH lookup unless an explicit executable is supplied."""
        default_args = self._parse("movie.mkv")
        explicit_args = self._parse("movie.mkv", "--ffmpeg-path", r"C:\tools\ffmpeg.exe")

        self.assertLoggedEqual("ffmpeg path default", None, default_args.ffmpeg_path)
        self.assertLoggedEqual("explicit ffmpeg path", r"C:\tools\ffmpeg.exe", explicit_args.ffmpeg_path)

    def test_postprocess_defaults_on(self):
        """Transcribed lines are cleaned by default."""
        args = self._parse("movie.mkv")

        self.assertLoggedEqual("postprocess on", True, args.postprocess)

    def test_no_postprocess_disables_cleaning(self):
        """Raw transcription text is available on request."""
        args = self._parse("movie.mkv", "--no-postprocess")

        self.assertLoggedEqual("postprocess off", False, args.postprocess)


class TestTranscribeCliExecution(LoggedTestCase):
    def _run(self, *argv : str, subtitles : Mock|None = None, error : SubtitleError|None = None):
        """Run the CLI with the transcription helpers replaced, returning (status, init mock, transcribe mock)."""
        subtitles = subtitles or Mock(linecount=1)

        with patch.object(transcribe, 'InitLogger'), \
                patch.object(transcribe, 'init_transcription', return_value=Mock()) as init_transcription, \
                patch.object(transcribe, 'transcribe_media', return_value=(subtitles, error)) as transcribe_media, \
                patch.object(transcribe, 'GetOutputPath', return_value='out.vtt'), \
                patch.object(sys, 'argv', ['transcribe.py', 'input.wav', *argv]):
            result = transcribe.main()

        return result, init_transcription, transcribe_media

    def test_ffmpeg_path_passes_to_transcription(self):
        """The CLI forwards an explicit executable path to transcription."""
        result, init_transcription, _transcribe = self._run('--ffmpeg-path', r'C:\tools\ffmpeg.exe')

        self.assertLoggedEqual("exit status", 0, result)
        self.assertLoggedEqual("ffmpeg path", r"C:\tools\ffmpeg.exe", init_transcription.call_args.kwargs['ffmpeg_path'])

    def test_provider_arguments_pass_to_transcription(self):
        """Provider, model, key and language hint are forwarded unchanged."""
        result, init_transcription, _transcribe = self._run('--provider', 'OpenAI', '-m', 'whisper-1', '-k', 'key', '--language', 'Chinese')

        self.assertLoggedEqual("exit status", 0, result)
        self.assertLoggedEqual("provider", "OpenAI", init_transcription.call_args.args[0])
        self.assertLoggedEqual("model", "whisper-1", init_transcription.call_args.kwargs['model'])
        self.assertLoggedEqual("api key", "key", init_transcription.call_args.kwargs['api_key'])
        self.assertLoggedEqual("language hint", "Chinese", init_transcription.call_args.kwargs['language'])

    @skip_if_debugger_attached
    def test_initialisation_failure_returns_nonzero_before_transcription(self):
        """Invalid provider settings or an unusable language stop the run before any audio is extracted."""
        with patch.object(transcribe, 'InitLogger'), \
                patch.object(transcribe, 'init_transcription', side_effect=SubtitleError("API Key is required")), \
                patch.object(transcribe, 'transcribe_media') as transcribe_media, \
                patch.object(sys, 'argv', ['transcribe.py', 'input.wav']):
            with self.assertLogs(level=logging.ERROR):
                result = transcribe.main()

        self.assertLoggedEqual("initialisation failure status", 1, result)
        self.assertLoggedEqual("transcription not started", 0, transcribe_media.call_count)

    def test_output_is_not_batched(self):
        """The CLI saves the transcription without preparing it for translation."""
        _result, _init, transcribe_media = self._run()

        self.assertLoggedEqual("auto batch", False, transcribe_media.call_args.kwargs['auto_batch'])

    def test_no_postprocess_passes_to_transcription(self):
        """Postprocessing can be disabled for plain output."""
        subtitles = Mock(linecount=1)
        result, init_transcription, _transcribe = self._run('--no-postprocess', subtitles=subtitles)

        self.assertLoggedEqual("exit status", 0, result)
        self.assertLoggedEqual("postprocess option", False, init_transcription.call_args.kwargs['postprocess_transcription'])
        subtitles.SaveOriginal.assert_called_once_with('out.vtt')

    def test_postprocess_defaults_on_for_plain_output(self):
        """Plain subtitle output retains the default cleaning option."""
        _result, init_transcription, _transcribe = self._run()

        self.assertLoggedEqual("default postprocess", True, init_transcription.call_args.kwargs['postprocess_transcription'])

    @skip_if_debugger_attached
    def test_save_failure_returns_nonzero(self):
        """An output write failure is reported as a failed CLI run."""
        subtitles = Mock(linecount=1)
        subtitles.SaveOriginal.side_effect = OSError("permission denied")

        with self.assertLogs(level=logging.ERROR):
            result, _init, _transcribe = self._run(subtitles=subtitles)

        self.assertLoggedEqual("save failure status", 1, result)

    def test_incomplete_run_saves_output_and_returns_nonzero(self):
        """Partial transcription output remains recoverable and is reported incomplete."""
        subtitles = Mock(linecount=1)

        with self.assertLogs(level=logging.ERROR):
            result, _init, _transcribe = self._run(subtitles=subtitles, error=SubtitleError("chunk failed"))

        self.assertLoggedEqual("incomplete status", 1, result)
        subtitles.SaveOriginal.assert_called_once_with('out.vtt')


if __name__ == '__main__':
    unittest.main()

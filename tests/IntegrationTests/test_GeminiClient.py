import importlib.util
import itertools
import logging
import unittest
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.SettingsType import SettingsType
from PySubtrans.Translation import Translation
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest

HAS_GEMINI = importlib.util.find_spec("google.genai") is not None

if HAS_GEMINI:
    from google.genai.types import Candidate, Content, FinishReason, GenerateContentResponse, Part

    from PySubtrans.Providers.Clients.GeminiClient import GeminiClient

_MODULE = 'PySubtrans.Providers.Clients.GeminiClient'

_PROMPT_CONTENT = "#1\nOriginal>\n안녕하세요\nTranslation>\n\n#2\nOriginal>\n잘 가요\nTranslation>\n"
_RESPONSE_BLOCK = "#1\nOriginal>\n안녕하세요\nTranslation>\nHello\n\n"


def _create_client(timeout : int = 0, max_retries : int = 0) -> 'GeminiClient':
    """Create a streaming Gemini client with a placeholder key."""
    return GeminiClient(SettingsType({
        'api_key': 'test-key',
        'instructions': 'Translate the subtitles.',
        'model': 'gemini-test',
        'supports_streaming': True,
        'stream_responses': True,
        'max_retries': max_retries,
        'backoff_time': 0.01,
        'timeout': timeout
    }))


def _create_request() -> TranslationRequest:
    """Create a streaming request for a two-line batch."""
    prompt = TranslationPrompt("Translate this", conversation=False)
    prompt.system_prompt = "Translate the subtitles."
    prompt.content = _PROMPT_CONTENT
    return TranslationRequest(prompt, streaming_callback=lambda _translation: None)


def _chunk(text : str|None, finish_reason : 'FinishReason|None' = None) -> 'GenerateContentResponse':
    """Build a streamed chunk, with no content parts when text is None."""
    content = Content(role="model", parts=[Part.from_text(text=text)]) if text is not None else None
    return GenerateContentResponse(candidates=[Candidate(content=content, finish_reason=finish_reason)])


def _mock_genai_client(chunks : Iterator['GenerateContentResponse']) -> MagicMock:
    """Return a genai.Client replacement whose stream yields the given chunks."""
    genai_client = MagicMock()
    genai_client.models.generate_content_stream.return_value = chunks
    return MagicMock(return_value=genai_client)


@unittest.skipUnless(HAS_GEMINI, "google-genai SDK is not installed")
class TestGeminiClientStreaming(LoggedTestCase):
    """Tests for how the Gemini client ends a streamed response."""

    def test_complete_stream(self) -> None:
        """A stream that finishes normally is reported as complete."""
        client = _create_client()
        chunks = iter([_chunk(_RESPONSE_BLOCK), _chunk(None, FinishReason.STOP)])

        with patch(f'{_MODULE}.genai.Client', _mock_genai_client(chunks)):
            translation = client._request_translation(_create_request())

        self.assertLoggedIsInstance("translation", translation, Translation)
        if translation:
            self.assertLoggedEqual("finish reason", "complete", translation.finish_reason)

    def test_token_limit_on_empty_final_chunk(self) -> None:
        """A token limit reported on a final chunk without parts is not lost."""
        client = _create_client()
        chunks = iter([_chunk(_RESPONSE_BLOCK), _chunk(None, FinishReason.MAX_TOKENS)])

        with patch(f'{_MODULE}.genai.Client', _mock_genai_client(chunks)):
            with self.assertLogs(level=logging.WARNING):
                translation = client._request_translation(_create_request())

        self.assertLoggedIsNotNone("translation", translation)
        if translation:
            self.assertLoggedTrue("reached token limit", translation.reached_token_limit)
            self.assertLoggedIsNotNone("partial text kept", translation.text)

    def test_slow_stream_is_stopped_at_timeout(self) -> None:
        """A stream that runs past the timeout is cut off and its partial text returned."""
        client = _create_client(timeout=120)
        chunks_consumed = 0
        clock = itertools.count(0.0, 45.0)

        def endless_chunks() -> Iterator['GenerateContentResponse']:
            nonlocal chunks_consumed
            while True:
                chunks_consumed += 1
                yield _chunk(_RESPONSE_BLOCK)

        with patch(f'{_MODULE}.genai.Client', _mock_genai_client(endless_chunks())):
            with patch(f'{_MODULE}.time.monotonic', side_effect=lambda: next(clock)):
                with self.assertLogs(level=logging.WARNING):
                    translation = client._request_translation(_create_request())

        self.assertLoggedIsNotNone("translation", translation)
        if translation:
            self.assertLoggedEqual("finish reason", "timeout", translation.finish_reason)
            self.assertLoggedIsNotNone("partial text kept", translation.text)

        self.assertLoggedLess("chunks consumed", chunks_consumed, 10)

    def test_timeout_passed_to_sdk(self) -> None:
        """The timeout also bounds the SDK request, in milliseconds."""
        client = _create_client(timeout=120)
        chunks = iter([_chunk(_RESPONSE_BLOCK), _chunk(None, FinishReason.STOP)])
        mock_client_class = _mock_genai_client(chunks)

        with patch(f'{_MODULE}.genai.Client', mock_client_class):
            client._request_translation(_create_request())

        http_options = mock_client_class.call_args.kwargs.get('http_options')
        self.assertLoggedEqual("sdk timeout", 120000, getattr(http_options, 'timeout', None))

    def test_retry_after_failed_stream_starts_afresh(self) -> None:
        """Text from a stream that failed part way is not carried into the retry."""
        client = _create_client(max_retries=1)

        def failing_chunks() -> Iterator['GenerateContentResponse']:
            yield _chunk(_RESPONSE_BLOCK)
            raise ConnectionError("Simulated stream failure")

        genai_client = MagicMock()
        genai_client.models.generate_content_stream.side_effect = [
            failing_chunks(),
            iter([_chunk(_RESPONSE_BLOCK), _chunk(None, FinishReason.STOP)])
        ]

        with patch(f'{_MODULE}.genai.Client', MagicMock(return_value=genai_client)):
            with self.assertLogs(level=logging.WARNING):
                translation = client._request_translation(_create_request())

        self.assertLoggedIsNotNone("translation", translation)
        if translation and translation.text:
            self.assertLoggedEqual("line blocks in response", 1, translation.text.count("#1"))

    def test_progress_reported_during_long_stream(self) -> None:
        """A long stream reports progress while it is still arriving."""
        client = _create_client()
        chunks = iter([_chunk(_RESPONSE_BLOCK), _chunk(_RESPONSE_BLOCK), _chunk(None, FinishReason.STOP)])
        clock = itertools.count(0.0, 45.0)

        with patch(f'{_MODULE}.genai.Client', _mock_genai_client(chunks)):
            with patch(f'{_MODULE}.time.monotonic', side_effect=lambda: next(clock)):
                with self.assertLogs(level=logging.INFO) as logs:
                    client._request_translation(_create_request())

        info_records = [record for record in logs.records if record.levelno == logging.INFO]
        self.assertLoggedGreater("progress messages", len(info_records), 0)


if __name__ == '__main__':
    unittest.main()

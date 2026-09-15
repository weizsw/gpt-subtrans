from unittest.mock import Mock

from GuiSubtrans.Commands.PostprocessTranslationsCommand import PostprocessTranslationsCommand
from GuiSubtrans.GuiSubtitleTestCase import GuiSubtitleTestCase
from GuiSubtrans.ProjectSelection import ProjectSelection, SelectionBatch, SelectionLine
from GuiSubtrans.Widgets.SelectionView import SelectionView
from PySubtrans.Helpers.TestCases import BuildSubtitlesFromLineCounts
from PySubtrans.SubtitleLine import SubtitleLine


class PostprocessTranslationsCommandTests(GuiSubtitleTestCase):
    """Test post-processing selected batches and its selection-view action."""

    def test_postprocesses_selected_lines_and_undo_restores_original_lines(self) -> None:
        self.options.update({
            'remove_filler_words': True,
            'break_long_lines': False,
            'break_dialog_on_one_line': False,
            'normalise_dialog_tags': False,
            'convert_wide_dashes': False,
            'full_width_punctuation': False,
        })

        subtitles = BuildSubtitlesFromLineCounts([[1, 1]])
        datamodel = self.create_project_datamodel(subtitles)

        for batch in subtitles.scenes[0].batches:
            batch.translated = [SubtitleLine.Construct(
                line.number,
                line.start,
                line.end,
                'um, translated line',
            ) for line in batch.originals]

        command = PostprocessTranslationsCommand([1, 2], datamodel)
        self.assertLoggedTrue('postprocess command executes', command.execute())

        processed_texts = [
            line.text
            for batch in subtitles.scenes[0].batches
            for line in batch.translated
        ]
        self.assertLoggedSequenceEqual(
            'all selected lines are postprocessed',
            ['translated line', 'translated line'],
            processed_texts,
        )
        self.assertLoggedEqual('one model update per batch', 2, len(command.model_updates))

        self.assertLoggedTrue('postprocess command undoes', command.undo())
        restored_texts = [
            line.text
            for batch in subtitles.scenes[0].batches
            for line in batch.translated
        ]
        self.assertLoggedSequenceEqual(
            'undo restores all translated lines',
            ['um, translated line', 'um, translated line'],
            restored_texts,
        )

    def test_postprocess_button_requires_all_selected_lines_translated(self) -> None:
        action_handler = Mock()
        view = SelectionView(action_handler)

        view.ShowSelection(ProjectSelection())
        self.assertLoggedFalse(
            'postprocess button is disabled without selected lines',
            view._postprocess_button.isEnabled(),
        )

        selection = ProjectSelection()
        selection.batches[(1, 1)] = SelectionBatch((1, 1), selected=True, translated=True)
        selection.lines[1] = SelectionLine(1, 1, 1, False, translated=True)
        view.ShowSelection(selection)
        self.assertLoggedTrue(
            'postprocess button is enabled when all selected lines are translated',
            view._postprocess_button.isEnabled(),
        )

        selection.batches[(1, 2)] = SelectionBatch((1, 2), selected=True, translated=False)
        selection.lines[2] = SelectionLine(1, 2, 2, False, translated=False)
        view.ShowSelection(selection)
        self.assertLoggedFalse(
            'postprocess button is disabled when a batch is untranslated',
            view._postprocess_button.isEnabled(),
        )

        view.deleteLater()

    def test_postprocesses_only_selected_lines_in_a_batch(self) -> None:
        self.options.update({
            'remove_filler_words': True,
            'break_long_lines': False,
            'break_dialog_on_one_line': False,
            'normalise_dialog_tags': False,
            'convert_wide_dashes': False,
            'full_width_punctuation': False,
        })

        subtitles = BuildSubtitlesFromLineCounts([[2]])
        datamodel = self.create_project_datamodel(subtitles)
        batch = subtitles.scenes[0].batches[0]
        batch.translated = [SubtitleLine.Construct(
            line.number,
            line.start,
            line.end,
            'um, translated line' if line.number == 2 else 'untouched line',
        ) for line in batch.originals]

        command = PostprocessTranslationsCommand([2], datamodel)
        self.assertLoggedTrue('partial postprocess command executes', command.execute())
        self.assertLoggedSequenceEqual(
            'only selected line is postprocessed',
            ['untouched line', 'translated line'],
            [line.text for line in batch.translated],
        )

        self.assertLoggedTrue('partial postprocess command undoes', command.undo())
        self.assertLoggedSequenceEqual(
            'undo restores unselected and selected lines',
            ['untouched line', 'um, translated line'],
            [line.text for line in batch.translated],
        )

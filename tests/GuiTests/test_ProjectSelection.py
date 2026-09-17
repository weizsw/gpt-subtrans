from GuiSubtrans.ProjectSelection import ProjectSelection, SelectionBatch, SelectionLine
from PySubtrans.Helpers.TestCases import LoggedTestCase


class ProjectSelectionTests(LoggedTestCase):
    """Test resolution of explicit line selections vs batch/scene fallback selections."""

    def test_effective_lines_prefers_explicit_selection_over_implicit_batch_lines(self) -> None:
        selection = ProjectSelection()

        # Simulate selecting a batch in the scenes tree: every line in the batch is
        # added to the selection, but only as an implicit (unselected) member.
        selection.batches[(1, 1)] = SelectionBatch((1, 1), selected=True, translated=True)
        selection.lines[1] = SelectionLine(1, 1, 1, False, translated=True)
        selection.lines[2] = SelectionLine(1, 1, 2, False, translated=False)

        self.assertLoggedSequenceEqual(
            'no explicit line selection uses every implicit line',
            [1, 2],
            sorted(line.number for line in selection.effective_lines),
        )

        # Simulate the user then explicitly selecting only one row in the content view.
        selection.AddSelectedLines([SelectionLine(1, 1, 1, True, translated=True)])

        self.assertLoggedSequenceEqual(
            'explicit line selection overrides implicit batch lines',
            [1],
            sorted(line.number for line in selection.effective_lines),
        )

    def test_all_lines_translated_ignores_untranslated_implicit_lines_when_a_line_is_selected(self) -> None:
        selection = ProjectSelection()
        selection.batches[(1, 1)] = SelectionBatch((1, 1), selected=True, translated=False)
        selection.lines[1] = SelectionLine(1, 1, 1, False, translated=True)
        selection.lines[2] = SelectionLine(1, 1, 2, False, translated=False)

        self.assertLoggedFalse(
            'batch with an untranslated implicit line is not all-translated',
            selection.AllLinesTranslated(),
        )

        selection.AddSelectedLines([SelectionLine(1, 1, 1, True, translated=True)])

        self.assertLoggedTrue(
            'explicitly selecting only the translated line ignores the untranslated implicit line',
            selection.AllLinesTranslated(),
        )

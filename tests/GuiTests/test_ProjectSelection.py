from GuiSubtrans.ProjectSelection import ProjectSelection, SelectionBatch, SelectionLine, SelectionScene
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

    def test_effective_lines_excludes_sibling_batches_touched_via_parent_scene_walk(self) -> None:
        selection = ProjectSelection()

        # Simulate AppendItem selecting batch (1, 1): it walks up to the parent scene to
        # register it, which recursively adds every sibling batch's lines as implicit
        # (unselected) members too - batch (1, 2) here was never selected by the user.
        selection.scenes[1] = SelectionScene(1, selected=False)
        selection.batches[(1, 1)] = SelectionBatch((1, 1), selected=True, translated=True)
        selection.lines[1] = SelectionLine(1, 1, 1, False, translated=True)
        selection.batches[(1, 2)] = SelectionBatch((1, 2), selected=False, translated=True)
        selection.lines[2] = SelectionLine(1, 2, 2, False, translated=True)

        self.assertLoggedSequenceEqual(
            'only lines from the explicitly selected batch are included',
            [1],
            sorted(line.number for line in selection.effective_lines),
        )

    def test_effective_lines_includes_all_batches_in_an_explicitly_selected_scene(self) -> None:
        selection = ProjectSelection()

        # Selecting the scene itself in the tree also adds its batches with selected=False,
        # so a whole-scene selection must be recognised via the scene flag, not the batches.
        selection.scenes[1] = SelectionScene(1, selected=True)
        selection.batches[(1, 1)] = SelectionBatch((1, 1), selected=False, translated=True)
        selection.lines[1] = SelectionLine(1, 1, 1, False, translated=True)
        selection.batches[(1, 2)] = SelectionBatch((1, 2), selected=False, translated=True)
        selection.lines[2] = SelectionLine(1, 2, 2, False, translated=True)

        self.assertLoggedSequenceEqual(
            'every line in the selected scene is included',
            [1, 2],
            sorted(line.number for line in selection.effective_lines),
        )

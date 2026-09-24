from PySide6.QtCore import Qt

from GuiSubtrans.Commands.MergeLinesCommand import MergeLinesCommand
from GuiSubtrans.GuiSubtitleTestCase import GuiSubtitleTestCase
from GuiSubtrans.ProjectSelection import ProjectSelection, SelectionBatch, SelectionScene
from GuiSubtrans.SubtitleListModel import SubtitleListModel
from GuiSubtrans.ViewModel.LineItem import LineItem
from GuiSubtrans.ViewModel.TestableViewModel import TestableViewModel


class SubtitleListModelTests(GuiSubtitleTestCase):
    """Tests for the flattened subtitle proxy model."""

    def test_merge_keeps_merged_line_visible(self) -> None:
        """Merging two visible lines must leave the merged row in the proxy."""
        subtitles = self.create_test_subtitles([[107, 4]])
        datamodel = self.create_project_datamodel(subtitles)
        viewmodel : TestableViewModel = self.create_testable_viewmodel(subtitles)
        datamodel.viewmodel = viewmodel

        proxy = SubtitleListModel(viewmodel)
        selection = ProjectSelection()
        selection.batches[(1, 2)] = SelectionBatch((1, 2), True)
        proxy.ShowSelection(selection)

        command = MergeLinesCommand([109, 110], datamodel)
        self.assertLoggedTrue("merge command executed", command.execute())
        for update in command.model_updates:
            datamodel.UpdateViewModel(update)
        viewmodel.ProcessUpdates()

        visible_items = [
            proxy.data(proxy.index(row, 0), Qt.ItemDataRole.UserRole)
            for row in range(proxy.rowCount())
        ]
        self.assertLoggedTrue(
            "visible rows are line items",
            all(isinstance(item, LineItem) for item in visible_items),
        )
        self.assertLoggedSequenceEqual(
            "visible line numbers after merge",
            [108, 109, 111],
            [item.number for item in visible_items if isinstance(item, LineItem)],
        )

        merged_item = viewmodel.GetLineItem(109)
        self.assertLoggedIsNotNone("merged line item", merged_item)
        if merged_item is not None:
            proxy_index = proxy.mapFromSource(viewmodel.indexFromItem(merged_item))
            self.assertLoggedEqual("merged line proxy row", 1, proxy_index.row())

        self.assertLoggedTrue("merge command undone", command.undo())
        for update in command.model_updates[1:]:
            datamodel.UpdateViewModel(update)
        viewmodel.ProcessUpdates()

        restored_items = [
            proxy.data(proxy.index(row, 0), Qt.ItemDataRole.UserRole)
            for row in range(proxy.rowCount())
        ]
        self.assertLoggedSequenceEqual(
            "visible line numbers after undo",
            [108, 109, 110, 111],
            [item.number for item in restored_items if isinstance(item, LineItem)],
        )

    def test_mixed_scene_and_batch_selection_shows_both(self) -> None:
        """Selecting a scene and a batch in another scene must show the lines of both."""
        viewmodel : TestableViewModel = self.create_testable_viewmodel_from_line_counts([[2, 2], [2, 2]])
        proxy = SubtitleListModel(viewmodel)

        selection = ProjectSelection()
        selection.scenes[1] = SelectionScene(1, selected=True)
        selection.batches[(1, 1)] = SelectionBatch((1, 1), selected=False)
        selection.batches[(1, 2)] = SelectionBatch((1, 2), selected=False)
        selection.scenes[2] = SelectionScene(2, selected=False)
        selection.batches[(2, 2)] = SelectionBatch((2, 2), selected=True)
        proxy.ShowSelection(selection)

        self.assertLoggedSequenceEqual(
            "visible batches",
            [(1, 1), (1, 2), (2, 2)],
            proxy.selected_batch_numbers,
        )

from __future__ import annotations

import logging

from GuiSubtrans.Command import Command, CommandError
from GuiSubtrans.ProjectDataModel import ProjectDataModel
from GuiSubtrans.ViewModel.ViewModelUpdate import ModelUpdate
from GuiSubtrans.ViewModel.ViewModelUpdateSection import BatchKey
from PySubtrans.Helpers import FormatNumberRanges
from PySubtrans.Helpers.Localization import _
from PySubtrans.SubtitleBatch import SubtitleBatch
from PySubtrans.SubtitleEditor import SubtitleEditor
from PySubtrans.SubtitleError import SubtitleError
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.SubtitleProcessor import SubtitleProcessor
from PySubtrans.Subtitles import Subtitles


class PostprocessTranslationsCommand(Command):
    """Post-process selected translated lines in one or more subtitle batches."""

    def __init__(self, line_numbers : list[int], datamodel : ProjectDataModel|None = None):
        super().__init__(datamodel)
        self.line_numbers : list[int] = sorted(set(line_numbers))
        self.undo_data : dict[int, str|None] = {}

    def execute(self) -> bool:
        """Run the configured subtitle post-processor on the selected lines."""
        if not self.line_numbers:
            raise CommandError(_("No lines selected to post-process"), command=self)

        if not self.datamodel or not self.datamodel.project:
            raise CommandError(_("No project data"), command=self)

        subtitles : Subtitles = self.datamodel.project.subtitles
        processor = SubtitleProcessor(self.datamodel.project_options)

        logging.info(_("Post-processing lines {lines}").format(
            lines=FormatNumberRanges(self.line_numbers)))

        self.ClearModelUpdates()
        self.undo_data = {}

        selected_lines = set(self.line_numbers)

        with SubtitleEditor(subtitles) as editor:
            try:
                batches = subtitles.GetBatchesContainingLines(self.line_numbers)
            except (SubtitleError, ValueError) as error:
                raise CommandError(_(
                    "Unable to find selected lines: {error}"
                ).format(error=error), command=self)

            found_lines = {line.number for batch in batches for line in batch.originals}
            if not selected_lines.issubset(found_lines):
                raise CommandError(_("Some selected lines were not found"), command=self)

            # Validate and post-process every batch before mutating any of them, so a
            # failure part-way through does not leave some batches changed with no undo data.
            processed_batches : list[tuple[SubtitleBatch, list[SubtitleLine]]] = []
            for batch in batches:
                original_selected_lines = [line for line in batch.originals if line.number in selected_lines]
                lines_to_process = [line for line in batch.translated if line.number in selected_lines]

                if (
                    len(lines_to_process) != len(original_selected_lines)
                    or any(line.text is None for line in lines_to_process)
                ):
                    raise CommandError(_(
                        "Some selected lines in scene {scene} batch {batch} are not translated"
                    ).format(scene=batch.scene, batch=batch.number), command=self)

                processed_batches.append((batch, processor.PostprocessSubtitles(lines_to_process)))

            for batch, processed_lines in processed_batches:
                model_update : ModelUpdate = self.AddModelUpdate()
                for processed_line in processed_lines:
                    previous_line = batch.GetTranslatedLine(processed_line.number)
                    self.undo_data[processed_line.number] = previous_line.text if previous_line else None

                    self._set_translation(editor, model_update, batch, processed_line.number, processed_line.text)

        return True

    def undo(self) -> bool:
        """Restore the translations captured before post-processing."""
        if not self.datamodel or not self.datamodel.project:
            raise CommandError(_("No project data"), command=self)

        if not self.undo_data:
            raise CommandError(_("No undo data available"), command=self)

        subtitles : Subtitles = self.datamodel.project.subtitles

        self.ClearModelUpdates()

        model_updates : dict[BatchKey, ModelUpdate] = {}

        with SubtitleEditor(subtitles) as editor:
            for line_number, previous_text in self.undo_data.items():
                batch : SubtitleBatch|None = subtitles.GetBatchContainingLine(line_number)
                if not batch:
                    raise CommandError(_("Line {line} not found in any batch").format(line=line_number), command=self)

                batch_key : BatchKey = (batch.scene, batch.number)
                model_update = model_updates.get(batch_key)
                if model_update is None:
                    model_update = self.AddModelUpdate()
                    model_updates[batch_key] = model_update

                self._set_translation(editor, model_update, batch, line_number, previous_text)

        return True

    def _set_translation(
        self, editor : SubtitleEditor, model_update : ModelUpdate, batch : SubtitleBatch, line_number : int, text : str|None
    ) -> None:
        """Update a line's translation and queue the corresponding view-model change."""
        editor.UpdateLine(line_number, {'translation': text})
        model_update.lines.update((batch.scene, batch.number, line_number), {'translation': text})

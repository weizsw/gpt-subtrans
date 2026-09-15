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
        self.undo_data : list[tuple[BatchKey, list[SubtitleLine]]] = []

    def execute(self) -> bool:
        """Run the configured subtitle post-processor on each selected batch."""
        if not self.line_numbers:
            raise CommandError(_("No lines selected to post-process"), command=self)

        if not self.datamodel or not self.datamodel.project:
            raise CommandError(_("No project data"), command=self)

        project = self.datamodel.project
        subtitles : Subtitles = project.subtitles
        processor = SubtitleProcessor(self.datamodel.project_options)

        logging.info(_("Post-processing lines {lines}").format(
            lines=FormatNumberRanges(self.line_numbers)))

        self.ClearModelUpdates()
        self.undo_data = []

        with SubtitleEditor(subtitles):
            batches : list[tuple[int, int, SubtitleBatch]] = []
            try:
                batches = [
                    (batch.scene, batch.number, batch)
                    for batch in subtitles.GetBatchesContainingLines(self.line_numbers)
                ]
            except (SubtitleError, ValueError) as error:
                raise CommandError(_(
                    "Unable to find selected lines: {error}"
                ).format(error=error), command=self)

            selected_lines = set(self.line_numbers)
            found_lines = {
                line.number
                for _, _, batch in batches
                for line in batch.originals
            }
            if not selected_lines.issubset(found_lines):
                raise CommandError(_("Some selected lines were not found"), command=self)

            processed_batches : list[tuple[int, int, SubtitleBatch, list[SubtitleLine], list[SubtitleLine]]] = []
            for scene_number, batch_number, batch in batches:
                original_lines = [line.copy() for line in batch.translated]
                lines_to_process = [line for line in batch.translated if line.number in selected_lines]

                original_selected_lines = [line for line in batch.originals if line.number in selected_lines]
                if (
                    len(lines_to_process) != len(original_selected_lines)
                    or any(line.text is None for line in lines_to_process)
                ):
                    raise CommandError(_(
                        "Some selected lines in scene {scene} batch {batch} are not translated"
                    ).format(scene=scene_number, batch=batch_number), command=self)

                processed_lines = processor.PostprocessSubtitles(lines_to_process)
                processed_batches.append((scene_number, batch_number, batch, original_lines, processed_lines))

            selected_line_numbers = set(self.line_numbers)

            for scene_number, batch_number, batch, original_lines, processed_lines in processed_batches:
                self.undo_data.append(((scene_number, batch_number), original_lines))
                processed_by_number = {line.number: line for line in processed_lines}
                batch.translated = [
                    processed_by_number.get(line.number, line)
                    if line.number in selected_line_numbers else line
                    for line in batch.translated
                ]
                self._update_viewmodel(batch, selected_line_numbers)

        return True

    def undo(self) -> bool:
        """Restore the translated lines captured before post-processing."""
        if not self.datamodel or not self.datamodel.project:
            raise CommandError(_("No project data"), command=self)

        if not self.undo_data:
            raise CommandError(_("No undo data available"), command=self)

        subtitles : Subtitles = self.datamodel.project.subtitles

        self.ClearModelUpdates()

        with SubtitleEditor(subtitles):
            for (scene_number, batch_number), original_lines in self.undo_data:
                try:
                    batch = subtitles.GetBatch(scene_number, batch_number)
                except SubtitleError as error:
                    raise CommandError(_(
                        "Unable to find scene {scene} batch {batch}: {error}"
                    ).format(scene=scene_number, batch=batch_number, error=error), command=self)

                batch.translated = [line.copy() for line in original_lines]
                self._update_viewmodel(batch, {line.number for line in original_lines})

        return True

    def _update_viewmodel(self, batch : SubtitleBatch, line_numbers : set[int]) -> None:
        """Queue translation text changes for the selected batch."""
        model_update: ModelUpdate = self.AddModelUpdate()
        for line in batch.translated:
            if line.number not in line_numbers:
                continue

            model_update.lines.update(
                (batch.scene, batch.number, line.number),
                {'translation': line.text}
            )

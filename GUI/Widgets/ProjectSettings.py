import logging
import os
from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QComboBox,
    QDialog,
    QFileDialog
)
from PySide6.QtCore import Signal, QSignalBlocker
from GUI.EditInstructionsDialog import EditInstructionsDialog
from GUI.ProjectActions import ProjectActions
from GUI.ProjectDataModel import ProjectDataModel

from GUI.Widgets.Widgets import OptionsGrid, TextBoxEditor
from PySubtitle.Helpers import GetValueName
from PySubtitle.Options import Options
from PySubtitle.Helpers.Parse import ParseNames
from PySubtitle.Substitutions import Substitutions
from PySubtitle.SubtitleFile import SubtitleFile
from PySubtitle.SubtitleProject import SubtitleProject
from PySubtitle.TranslationProvider import TranslationProvider

class ProjectSettings(QGroupBox):
    """
    Allow the user to edit project-specific settings
    """
    settingsChanged = Signal(dict)

    def __init__(self, action_handler : ProjectActions = None, parent=None):
        super().__init__(parent=parent)
        self.setTitle("Project Settings")
        self.setMinimumWidth(450)

        self.action_handler = action_handler
        self.provider_list = sorted(TranslationProvider.get_providers())
        self.model_list = []
        self.widgets = {}
        self.settings = {}
        self.current_provider = None
        self.datamodel = None
        self.updating_model_list = False

        self.layout = QVBoxLayout(self)
        self.grid_layout = OptionsGrid()

        self.layout.addLayout(self.grid_layout)

    def GetSettings(self):
        """
        Get a dictionary of the user's settings
        """
        settings = {
            'movie_name': self.widgets['movie_name'].text(),
            'target_language': self.widgets['target_language'].text(),
            'add_right_to_left_markers': self.widgets['add_right_to_left_markers'].isChecked(),
            'include_original': self.widgets['include_original'].isChecked(),
            'description': self.widgets['description'].toPlainText(),
            'names': ParseNames(self.widgets['names'].toPlainText()),
            'substitutions': Substitutions.Parse(self.widgets['substitutions'].toPlainText()),
            'substitution_mode': self.widgets['substitution_mode'].currentText(),
            'model': self.widgets['model'].currentText() if 'model' in self.widgets else self.settings.get('model'),
            'provider': self.widgets['provider'].currentText() if 'provider' in self.widgets else self.settings.get('provider'),
        }

        return settings
    
    def UpdateSettings(self):
        """
        Update the project settings with the current values
        """
        settings = self.GetSettings()
        self.settings.update(settings)

    def OpenSettings(self):
        self._update_available_models()

        self.show()

    def SetDataModel(self, datamodel : ProjectDataModel):
        self.datamodel = datamodel
        self.current_provider = datamodel.provider
        try:
            self.model_list = datamodel.available_models
        except Exception as e:
            logging.warning(f"Unable to retrieve models: {e}")
            self.model_list = []

        self.settings = datamodel.project.GetProjectSettings()
        self.settings['model'] = datamodel.selected_model
        self.settings['provider'] = datamodel.provider
        self.settings['project_path'] = os.path.dirname(datamodel.project.projectfile)
        self.BuildForm(self.settings)

    def Populate(self):
        with QSignalBlocker(self):
            for key in self.settings:
                if key in self.widgets:
                    value = self.settings.get(key)
                    self._setvalue(key, value)

    def BuildForm(self, settings : dict):
        self.ClearForm()
        with QSignalBlocker(self):
            self.AddSingleLineOption("Movie Name", settings, 'movie_name')
            self.AddSingleLineOption("Target Language", settings, 'target_language')
            self.AddCheckboxOption("Add RTL Markers", settings, 'add_right_to_left_markers')
            self.AddCheckboxOption("Include Original Text", settings, 'include_original')
            self.AddMultiLineOption("Description", settings, 'description')
            self.AddMultiLineOption("Names", settings, 'names')
            self.AddMultiLineOption("Substitutions", settings, 'substitutions')
            self.AddDropdownOption("Substitution Mode", settings, 'substitution_mode', Substitutions.Mode)
            self.AddButton("", "Edit Instructions", self._edit_instructions)
            self.AddButton("", "Copy From Another Project", self._copy_from_another_project)
            if len(self.provider_list) > 1:
                self.AddDropdownOption("Provider", settings, 'provider', self.provider_list)
            if len(self.model_list) > 0:
                self.AddDropdownOption("Model", settings, 'model', self.model_list)

    def ClearForm(self):
        self.current_row = 0
        self.widgets = {}
        with QSignalBlocker(self):
            # Remove and delete all widgets from the form layout
            for i in reversed(range(self.grid_layout.count())):
                widget = self.grid_layout.itemAt(i).widget()
                if widget is not None:
                    widget.deleteLater()

    def AddSingleLineOption(self, label, settings, key):
        # Add label and input field for a single-line option
        label_widget = QLabel(label)
        input_widget = QLineEdit()
        self._settext(input_widget, settings.get(key, ""))
        self._add_row(key, label_widget, input_widget)
        input_widget.editingFinished.connect(self._text_changed)

    def AddMultiLineOption(self, label, settings, key):
        # Add label and input field for a multi-line option
        label_widget = QLabel(label)
        input_widget = TextBoxEditor()
        input_widget.setAcceptRichText(False)
        self._settext(input_widget, settings.get(key, ""))

        self._add_row(key, label_widget, input_widget)
        input_widget.editingFinished.connect(self._text_changed)

    def AddCheckboxOption(self, label, settings, key):
        label_widget = QLabel(label)
        input_widget = QCheckBox(self)
        value = settings.get(key, False)
        input_widget.setChecked(value)
        self._add_row(key, label_widget, input_widget)
        input_widget.stateChanged.connect(self._check_changed)

    def AddDropdownOption(self, label, settings, key, values):
        label_widget = QLabel(label)
        combo_box = QComboBox(self)
        combo_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for value in values:
            value_name = GetValueName(value)
            combo_box.addItem(value_name)

        if key in settings:
            initial_value = settings[key]
            if hasattr(initial_value, 'name'):
                initial_value = initial_value.name
            initial_index = combo_box.findText(initial_value)
            if initial_index >= 0:
                combo_box.setCurrentIndex(initial_index)

        combo_box.currentTextChanged.connect(lambda x: self._option_changed(key, x))
        self._add_row(key, label_widget, combo_box)

    def AddButton(self, label, text, callable):
        label_widget = QLabel(label)
        button_widget = QPushButton(text)
        button_widget.clicked.connect(callable)
        self.grid_layout.addWidget(label_widget, self.current_row, 0)
        self.grid_layout.addWidget(button_widget, self.current_row, 1)
        self.current_row += 1

    def _add_row(self, key, label_widget, input_widget):
        self.grid_layout.addWidget(label_widget, self.current_row, 0)
        self.grid_layout.addWidget(input_widget, self.current_row, 1)
        self.widgets[key] = input_widget
        self.current_row += 1

    def _setvalue(self, key, value):
        widget = self.widgets.get(key)
        if isinstance(widget, QCheckBox):
            widget.setChecked(value or False)
        elif isinstance(widget, QComboBox):
            self._update_combo_box(widget, value)
        elif widget is not None:
            self._settext(widget, value)
        else:
            raise ValueError(f"No widget for key {key}")

    def _settext(self, widget, value):
        if isinstance(value, list):
            value = '\n'.join(value)
        elif isinstance(value, dict):
            items = [ f"{k}::{v}" for k, v in value.items() ]
            value = '\n'.join(items)
        widget.setText(value or "")

    def _update_combo_box(self, widget, value):
        index = widget.findText(value)
        if index >= 0:
            widget.setCurrentIndex(index)

    def _text_changed(self, text = None):
        self.UpdateSettings()
        self.settingsChanged.emit(self.settings)

    def _check_changed(self, int = None):
        self.UpdateSettings()
        self.settingsChanged.emit(self.settings)

    def _option_changed(self, key, value):
        if key == 'provider':
            self._update_provider_settings(value)
            self.datamodel.SaveProject()
        elif key == 'model' and not self.updating_model_list:
            if value and value != self.settings.get('model'):
                self.datamodel.UpdateProjectSettings({ "model": value })
                self.settings['model'] = self.datamodel.selected_model

    def _update_provider_settings(self, provider : str):
        try:
            self.datamodel.UpdateProjectSettings({ "provider": provider})
            self.action_handler.CheckProviderSettings()
            self.model_list = self.datamodel.available_models
            self.settings['provider'] = provider
            self.settings['model'] = self.datamodel.selected_model
            self._update_available_models()

        except Exception as e:
            logging.error(f"Provider error: {e}")

    def _update_available_models(self):
        model_input = self.widgets.get('model')
        if model_input:
            try:
                self.updating_model_list = True
                model_input.clear()
                model_input.addItems(self.model_list)
                self._update_combo_box(model_input, self.settings.get('model'))

            except Exception as e:
                logging.error(f"Error updating model list: {e}")
            finally:
                self.updating_model_list = False

    def _edit_instructions(self):
        # Commit the settings
        self.UpdateSettings()

        dialog = EditInstructionsDialog(self.settings, parent=self)
        result = dialog.exec()

        if result == QDialog.Accepted:
            logging.info("Instructions for this project updated\n")
            self.settings.update(dialog.instructions.GetSettings())
            self.settingsChanged.emit(dialog.instructions.GetSettings())
            self.BuildForm(self.settings)

    def _copy_from_another_project(self):
        '''
        Copy project settings from another project file
        '''
        dialog_options = QFileDialog.Options()
        initial_path = self.settings.get('project_path') or self.settings.get('last_used_path')
        filter = "Subtrans Files (*.subtrans);;All Files (*)"
        caption = "Select project to copy settings from"
        file_name, _ = QFileDialog.getOpenFileName(self, caption, dir=initial_path, filter=filter, options=dialog_options)
        if file_name:
            try:
                project_options = Options({"project": 'read'})
                source : SubtitleProject = SubtitleProject(project_options)
                subtitles : SubtitleFile = source.ReadProjectFile(file_name)
                if not subtitles:
                    raise ValueError("Invalid project file")

                # Don't copy provider because we don't have the settings for it (including model list)
                subtitles.settings.pop('provider', None)
                subtitles.settings.pop('model', None)

                # Don't copy instructions, they're too hidden
                subtitles.settings.pop('instructions', None)
                subtitles.settings.pop('retry_instructions', None)
                subtitles.settings.pop('prompt', None)
                subtitles.settings.pop('task_type', None)
                subtitles.settings.pop('instruction_file', None)

                self.settings.update(subtitles.settings)
                self.Populate()

            except Exception as e:
                logging.error(f"Unable to read project file: {str(e)}")

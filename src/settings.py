# settings.py
import gi
import os
import re
import subprocess
import threading
import yaml
import shutil
from pathlib import Path
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Gio, Gdk, Adw, GObject

from helpers import human_path as _hp

def load_settings(self):
    self.theme_index = 0
    self.output_directory = os.path.expanduser("~/Downloads")
    self.ts_enabled = True
    self.selected_model = ''

    if self.settings_file.exists():
        try:
            with open(self.settings_file, 'r') as f:
                settings = yaml.safe_load(f) or {}
            self.theme_index = settings.get('theme', 0)
            self.output_directory = settings.get('output_directory', os.path.expanduser("~/Downloads"))
            self.ts_enabled = settings.get('include_timestamps', True)
            self.selected_model = settings.get('model', '')
        except Exception as e:
            self._error(f"Error loading settings: {e}")

    style_manager = Adw.StyleManager.get_default()
    themes = [Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK]
    style_manager.set_color_scheme(themes[self.theme_index])

def save_settings(self):
    settings = {
        'theme': self.theme_index,
        'model': self.display_to_core.get(self.model_strings.get_string(self.model_combo.get_selected()), ''),
        'output_directory': self.output_directory or os.path.expanduser("~/Downloads"),
        'include_timestamps': self.ts_enabled
    }
    try:
        os.makedirs(self.settings_file.parent, exist_ok=True)
        with open(self.settings_file, 'w') as f:
            yaml.dump(settings, f, default_style="'", default_flow_style=False)
    except Exception as e:
        self._error(f"Error saving settings: {e}")

def _on_timestamps_toggled(self, switch, _):
    self.ts_enabled = switch.get_active()
    self.save_settings()
    action = self.lookup_action("toggle-timestamps")
    if action:
        action.set_state(GLib.Variant.new_boolean(self.ts_enabled))

def on_settings(self, action, param):
    dlg = Adw.PreferencesDialog()
    dlg.set_title("Settings")
    dlg.set_size_request(480, 640)
    self.settings_dialog = dlg
    page = Adw.PreferencesPage()
    page.set_title("General")
    page.set_icon_name("preferences-system-symbolic")
    dlg.add(page)

    appearance_group = Adw.PreferencesGroup()
    appearance_group.set_title("Appearance")
    appearance_group.set_description("Customize the application appearance")
    theme_row = Adw.ComboRow()
    theme_row.set_title("Theme")
    theme_row.set_subtitle("Choose application theme")
    theme_model = Gtk.StringList()
    theme_model.append("System")
    theme_model.append("Light")
    theme_model.append("Dark")
    theme_row.set_model(theme_model)
    theme_row.set_selected(self.theme_index)
    theme_row.connect("notify::selected", self._on_theme_changed)
    appearance_group.add(theme_row)
    page.add(appearance_group)

    output_group = Adw.PreferencesGroup()
    output_group.set_title("Output")
    self.output_settings_row = Adw.ActionRow()
    self.output_settings_row.set_title("Output Directory")
    self.output_settings_row.set_subtitle(_hp(self.output_directory))
    browse_settings_btn = Gtk.Button()
    browse_settings_btn.set_icon_name("folder-open-symbolic")
    browse_settings_btn.set_valign(Gtk.Align.CENTER)
    browse_settings_btn.add_css_class("flat")
    browse_settings_btn.connect("clicked", self._browse_out_settings)
    self.output_settings_row.add_suffix(browse_settings_btn)
    output_group.add(self.output_settings_row)
    page.add(output_group)

    model_group = Adw.PreferencesGroup()
    model_group.set_title("AI Model")
    model_group.set_description("Select and manage transcription models")
    model_row = Adw.ComboRow()
    model_row.set_title("Model")
    model_row.set_subtitle("Choose transcription model")
    model_row.set_model(self.model_strings)
    model_row.connect("notify::selected", self._on_model_combo_changed)
    model_group.add(model_row)
    self.model_combo = model_row
    model_action_row = Adw.ActionRow()
    model_action_row.set_title("Model Management")
    model_action_row.set_subtitle("Install or remove the selected model")
    self.model_btn = Gtk.Button()
    self.model_btn.set_valign(Gtk.Align.CENTER)
    self.model_btn.add_css_class("pill")
    self.model_btn.connect("clicked", self.on_model_btn)
    model_action_row.add_suffix(self.model_btn)
    model_group.add(model_action_row)
    page.add(model_group)

    transcription_group = Adw.PreferencesGroup()
    transcription_group.set_title("Transcription")
    transcription_group.set_description("Configure transcription options")
    timestamps_row = Adw.SwitchRow()
    timestamps_row.set_title("Include Timestamps")
    timestamps_row.set_subtitle("Add timestamps to transcription output")
    timestamps_row.set_active(self.ts_enabled)
    timestamps_row.connect("notify::active", self._on_timestamps_toggled)
    transcription_group.add(timestamps_row)
    page.add(transcription_group)

    self._refresh_model_menu()
    self._update_model_btn()

    dlg.connect("destroy", lambda d: setattr(self, 'settings_dialog', None))
    dlg.present(self.window)

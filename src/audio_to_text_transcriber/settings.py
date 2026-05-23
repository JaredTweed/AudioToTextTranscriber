# settings.py
import gi
import os
import yaml
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Gio, Gdk, Adw, GObject

from .helpers import human_path as _hp

def _as_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return default if value is None else bool(value)

def _is_document_portal_path(path):
    if not path:
        return False
    portal_root = os.path.join(GLib.get_user_runtime_dir(), "doc")
    path = os.path.abspath(path)
    return path == portal_root or path.startswith(portal_root + os.sep)

def _common_output_directories():
    dirs = []
    for user_dir in (
        GLib.UserDirectory.DIRECTORY_DOWNLOAD,
        GLib.UserDirectory.DIRECTORY_DOCUMENTS,
        GLib.UserDirectory.DIRECTORY_DESKTOP,
        GLib.UserDirectory.DIRECTORY_MUSIC,
        GLib.UserDirectory.DIRECTORY_VIDEOS,
    ):
        path = GLib.get_user_special_dir(user_dir)
        if path:
            dirs.append(os.path.abspath(path))
    dirs.append("/tmp")
    return dirs

def _document_portal_target(path):
    if not _is_document_portal_path(path):
        return None

    portal_root = os.path.join(GLib.get_user_runtime_dir(), "doc")
    try:
        rel_parts = os.path.relpath(os.path.abspath(path), portal_root).split(os.sep)
    except ValueError:
        return None

    if len(rel_parts) < 2 or rel_parts[0] in ("by-app", ".", ".."):
        return None

    exposed_parts = rel_parts[1:]
    exposed_name = exposed_parts[0]
    for base in _common_output_directories():
        if os.path.basename(base) == exposed_name:
            return os.path.join(base, *exposed_parts[1:])
    return None

def _normal_output_directory(self, path=None):
    default_output = getattr(
        self,
        "default_output_directory",
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        or os.path.join(os.path.expanduser("~"), "Downloads"),
    )
    path = path or default_output
    old_sandbox_default = os.path.join(
        GLib.get_user_data_dir(),
        "AudioToTextTranscriber",
        "transcripts",
    )
    if os.path.abspath(os.path.expanduser(path)) == os.path.abspath(old_sandbox_default):
        return default_output
    if _is_document_portal_path(path):
        portal_target = _document_portal_target(path)
        if portal_target:
            return portal_target
        return default_output
    return os.path.abspath(os.path.expanduser(path))

def load_settings(self):
    self.theme_index = 0
    default_output = _normal_output_directory(self)
    os.makedirs(default_output, exist_ok=True)
    self.output_directory = default_output
    self.ts_enabled = True
    self.selected_model = ''

    if self.settings_file.exists():
        try:
            with open(self.settings_file, 'r') as f:
                settings = yaml.safe_load(f) or {}
            self.theme_index = int(settings.get('theme', 0))
            self.output_directory = _normal_output_directory(
                self,
                settings.get('output_directory', default_output)
            )
            self.ts_enabled = _as_bool(settings.get('include_timestamps', True), True)
            self.selected_model = settings.get('model', '') or ''
        except Exception as e:
            print(f"Error loading settings: {e}")

    self.theme_index = min(max(self.theme_index, 0), 2)
    if not os.path.isdir(self.output_directory) or not os.access(self.output_directory, os.W_OK):
        self.output_directory = default_output

    style_manager = Adw.StyleManager.get_default()
    themes = [Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK]
    style_manager.set_color_scheme(themes[self.theme_index])

def _selected_model_core(self):
    if not getattr(self, "model_combo", None) or not getattr(self, "model_strings", None):
        return getattr(self, "selected_model", "")
    selected = self.model_combo.get_selected()
    if selected == Gtk.INVALID_LIST_POSITION or selected >= self.model_strings.get_n_items():
        return getattr(self, "selected_model", "")
    return self.display_to_core.get(self.model_strings.get_string(selected), "")

def save_settings(self):
    self.selected_model = self._selected_model_core()
    self.output_directory = _normal_output_directory(self, self.output_directory)
    settings = {
        'theme': self.theme_index,
        'model': self.selected_model,
        'output_directory': self.output_directory,
        'include_timestamps': self.ts_enabled
    }
    try:
        os.makedirs(self.settings_file.parent, exist_ok=True)
        with open(self.settings_file, 'w') as f:
            yaml.safe_dump(settings, f, default_flow_style=False)
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
    dlg.set_size_request(300, 150)
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
    self.model_combo = model_row
    model_row.connect("notify::selected", self._on_model_combo_changed)
    model_group.add(model_row)
    model_action_row = Adw.ActionRow()
    model_action_row.set_title("Model Management")
    model_action_row.set_subtitle("Install or remove the selected model")
    self.model_btn = Gtk.Button()
    self.model_btn.set_valign(Gtk.Align.CENTER)
    self.model_btn.add_css_class("pill")
    self.model_btn.connect("clicked", self.on_model_btn)
    model_action_row.add_suffix(self.model_btn)
    model_group.add(model_action_row)
    self.model_action_row = model_action_row
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

    self.timestamps_row = timestamps_row

    self._refresh_model_menu()
    self._update_model_btn()

    dlg.connect("destroy", lambda d: setattr(self, 'settings_dialog', None))
    self._set_settings_lock(bool(getattr(self, 'is_transcribing', False)))
    dlg.present(self.window)


def _set_settings_lock(self, locked: bool):
    """
    When locked=True (transcribing), disable every control except Appearance.
    """
    for w in (
        getattr(self, 'output_settings_row', None),  # Output directory
        getattr(self, 'model_combo', None),          # Model dropdown
        getattr(self, 'model_btn', None),            # Install/Delete button
        getattr(self, 'model_action_row', None),     # << NEW: grey out the whole row
        getattr(self, 'timestamps_row', None),       # Include timestamps
    ):
        if w:
            w.set_sensitive(not locked)

def _unlock_settings_now(self):
    """
    Clear the transcribing flag, re-enable the menu toggle, and
    restore all Settings controls immediately on the main loop.
    """
    self.is_transcribing = False

    # re-enable “Include-timestamps” menu item
    act = self.lookup_action("toggle-timestamps")
    if act:
        act.set_enabled(True)

    # if the Settings dialog is open, flip its widgets back on
    if getattr(self, "settings_dialog", None):
        GLib.idle_add(self._set_settings_lock, False)

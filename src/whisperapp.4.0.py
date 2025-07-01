import gi
import os
import subprocess
import threading
import yaml
import shutil
import sounddevice as sd
import wave
import tempfile
from pathlib import Path
from queue import Queue
from datetime import datetime
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Gio, Gdk, Adw, GObject

MB = 1024 * 1024

MODEL_SIZE_MB = {
    "tiny":   75,
    "base":   142,
    "small":  466,
    "medium": 1536,
    "large":  2960
}

class WhisperApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.JaredTweed.AudioToTextTranscriber")
        self.title = "Audio-To-Text Transcriber"
        self.settings_file = Path(GLib.get_user_data_dir()) / "AudioToTextTranscriber" / "Settings.yaml"
        self.load_settings()
        self.ts_enabled = True
        sd = os.path.abspath(os.path.dirname(__file__))
        self.repo_dir = os.path.join(sd, "whisper.cpp") if os.path.isdir(os.path.join(sd, "whisper.cpp")) else sd
        self.bin_path = shutil.which("whisper-cli") or os.path.join(self.repo_dir, "build", "bin", "whisper-cli")
        self.download_script = os.path.join(self.repo_dir, "models", "download-ggml-model.sh")
        data_dir = os.getenv(
            "AUDIO_TO_TEXT_TRANSCRIBER_DATA_DIR",
            os.path.join(GLib.get_user_data_dir(), "AudioToTextTranscriber")
        )
        os.makedirs(data_dir, exist_ok=True)
        self.models_dir = os.path.join(data_dir, "models")
        os.makedirs(self.models_dir, exist_ok=True)
        self.display_to_core = {}
        self.dl_info = None
        self.cancel_flag = False
        self.current_proc = None
        self.desired_models = ["tiny", "tiny.en", "base", "base.en", "small", "small.en",
                              "medium", "medium.en", "large-v1", "large-v2", "large-v3",
                              "large-v3-turbo"]
        self.audio_store = Gtk.StringList()
        self.progress_items = []
        self.files_group = None
        self.main_content = None
        self.navigation_view = None
        self.header_bar = None
        self.back_button = None
        self.live_trans_button = None  # Reference to live transcription button
        # Live transcription variables
        self.live_transcribing = False
        self.audio_queue = Queue()
        self.temp_audio_dir = tempfile.mkdtemp()
        self.live_transcription_buffer = None
        self.live_transcription_view = None
        self.audio_stream = None
        self.connect('startup', self.do_startup)
        self.connect('activate', self.do_activate)
        self.create_action("settings", self.on_settings)

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
                GLib.idle_add(self.update_status_card)
            except Exception as e:
                self._error(f"Error loading settings: {e}")

        style_manager = Adw.StyleManager.get_default()
        themes = [Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK]
        style_manager.set_color_scheme(themes[self.theme_index])

    def do_startup(self, *args):
        Adw.Application.do_startup(self)
        self.window = Adw.ApplicationWindow(application=self, title=self.title)
        self.window.set_default_size(600, 800)
        self._build_ui()
        self._setup_dnd()
        self._update_model_btn()
        self._update_download_progress()

    def do_activate(self, *args):
        self.window.present()

    def create_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def create_navigation_ui(self, main_box):
        self.navigation_view = Adw.NavigationView()
        self.navigation_view.set_vexpand(True)
        self.navigation_view.set_hexpand(True)

        # Welcome Page
        welcome_page = Adw.NavigationPage()
        welcome_page.set_title("Welcome")
        welcome_page.set_tag("welcome")
        welcome_scrolled = Gtk.ScrolledWindow()
        welcome_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        welcome_scrolled.set_vexpand(True)
        welcome_scrolled.set_hexpand(True)

        self.main_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.main_content.set_margin_start(12)
        self.main_content.set_margin_end(12)
        self.main_content.set_margin_top(6)
        self.main_content.set_margin_bottom(6)

        self.status_card = Adw.StatusPage()
        self.status_card.set_title("Add Audio Files")
        self.status_card.set_icon_name("audio-x-generic-symbolic")
        self.update_status_card()
        self.main_content.append(self.status_card)

        welcome_scrolled.set_child(self.main_content)
        welcome_page.set_child(welcome_scrolled)
        self.navigation_view.add(welcome_page)

        # File Review Page
        review_page = Adw.NavigationPage()
        review_page.set_title("Review Files")
        review_page.set_tag("review")
        review_scrolled = Gtk.ScrolledWindow()
        review_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        review_scrolled.set_vexpand(True)
        review_scrolled.set_hexpand(True)

        review_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        review_content.set_margin_start(6)
        review_content.set_margin_end(6)
        review_content.set_margin_top(6)
        review_content.set_margin_bottom(6)

        self.trans_btn = Gtk.Button(label="Transcribe")
        self._green(self.trans_btn)
        self.trans_btn.connect("clicked", self.on_transcribe)
        review_content.append(self.trans_btn)

        add_more_button = Gtk.Button(label="Add More...")
        add_more_button.connect("clicked", self.on_add_audio)
        review_content.append(add_more_button)

        self.files_group = Adw.PreferencesGroup()
        self.files_group.set_title("Audio Files")
        self.files_group.set_description("Review files to be transcribed")
        review_content.append(self.files_group)

        review_content_clamp = Adw.Clamp()
        review_content_clamp.set_child(review_content)
        review_scrolled.set_child(review_content_clamp)
        review_page.set_child(review_scrolled)
        self.navigation_view.add(review_page)

        # File Details Page
        details_page = Adw.NavigationPage()
        details_page.set_title("File Details")
        details_page.set_tag("details")
        details_scrolled = Gtk.ScrolledWindow()
        details_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        details_scrolled.set_vexpand(True)
        details_scrolled.set_hexpand(True)

        details_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        details_content.set_margin_start(12)
        details_content.set_margin_end(12)
        details_content.set_margin_top(6)
        details_content.set_margin_bottom(6)
        details_page.set_child(details_scrolled)
        self.navigation_view.add(details_page)

        # Live Transcription Page
        live_page = Adw.NavigationPage()
        live_page.set_title("Live Transcription")
        live_page.set_tag("live")
        live_scrolled = Gtk.ScrolledWindow()
        live_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        live_scrolled.set_vexpand(True)
        live_scrolled.set_hexpand(True)

        live_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        live_content.set_margin_start(12)
        live_content.set_margin_end(12)
        live_content.set_margin_top(12)
        live_content.set_margin_bottom(12)

        self.live_transcription_buffer = Gtk.TextBuffer()
        self.live_transcription_view = Gtk.TextView.new_with_buffer(self.live_transcription_buffer)
        self.live_transcription_view.set_editable(False)
        self.live_transcription_view.set_monospace(True)
        self.live_transcription_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.live_transcription_view.set_vexpand(True)
        self.live_transcription_view.set_hexpand(True)

        scrolled_text = Gtk.ScrolledWindow()
        scrolled_text.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_text.set_child(self.live_transcription_view)
        scrolled_text.set_vexpand(True)
        scrolled_text.add_css_class("card")

        live_content.append(scrolled_text)

        self.live_status_label = Gtk.Label(label="Ready to start live transcription")
        live_content.append(self.live_status_label)

        live_content_clamp = Adw.Clamp()
        live_content_clamp.set_child(live_content)
        live_scrolled.set_child(live_content_clamp)
        live_page.set_child(live_scrolled)
        self.navigation_view.add(live_page)

        self.navigation_view.connect("pushed", self._on_navigation_pushed)
        self.navigation_view.connect("popped", self._on_navigation_popped)

        main_box.append(self.navigation_view)
        return self.navigation_view

    def _on_navigation_pushed(self, navigation_view, *args):
        tag = navigation_view.get_visible_page().get_tag()
        self._update_navigation_buttons(tag)

    def _on_navigation_popped(self, navigation_view, *args):
        tag = navigation_view.get_visible_page().get_tag()
        self._update_navigation_buttons(tag)

    def _update_navigation_buttons(self, tag):
        if tag == "welcome":
            self.revert_button.set_visible(False)
            self.back_button.set_visible(False)
        elif tag == "review":
            self.back_button.set_visible(False)
            self.revert_button.set_visible(True)
        elif tag == "details":
            self.revert_button.set_visible(False)
            self.back_button.set_visible(True)
            self.back_button.set_label("< Back")
        elif tag == "live":
            self.revert_button.set_visible(False)
            self.back_button.set_visible(True)
            self.back_button.set_label("< Back")

    def _on_icon_dnd_drop(self, drop_target, value, x, y, button):
        files = value.get_files()
        new_paths = self._collect_audio_files(files)
        for path in new_paths:
            if path not in [self.audio_store.get_string(i) for i in range(self.audio_store.get_n_items())]:
                self.audio_store.append(path)
                self.add_file_to_list(os.path.basename(path), path)
        if new_paths:
            toast = Adw.Toast(title=f"Added {len(new_paths)} file(s)")
            toast.set_timeout(3)
            self.toast_overlay.add_toast(toast)
            if self.navigation_view.get_visible_page().get_tag() != "review":
                GLib.idle_add(self.navigation_view.push_by_tag, "review")
        return True

    def update_status_card(self, transcription_status=None):
        if self.status_card.get_child():
            self.status_card.set_child(None)

        if transcription_status:
            if transcription_status == "started":
                self.status_card.set_title("Transcription Started")
                self.status_card.set_icon_name("media-playback-start-symbolic")
                description = "Transcribing files..."
            elif transcription_status == "completed":
                self.status_card.set_title("Transcription Completed")
                self.status_card.set_icon_name("emblem-ok-symbolic")
                description = "All transcriptions completed."
            elif transcription_status == "cancelled":
                self.status_card.set_title("Transcription Cancelled")
                self.status_card.set_icon_name("dialog-error-symbolic")
                description = "Transcription was cancelled."
            elif transcription_status == "error":
                self.status_card.set_title("Transcription Error")
                self.status_card.set_icon_name("dialog-error-symbolic")
                description = "An error occurred during transcription."
        else:
            self.status_card.set_title("Audio-To-Text Transcriber")
            self.status_card.set_icon_name("audio-x-generic-symbolic")
            description = "Select audio files or folders to transcribe, or start live transcription."

        self.status_card.set_description(description)

        main_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_content_box.set_margin_top(12)
        main_content_box.set_margin_bottom(12)

        if not transcription_status:
            button_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            button_container.set_halign(Gtk.Align.CENTER)
            button_container.set_margin_top(6)
            button_container.set_margin_bottom(12)

            add_files_button = Gtk.Button()
            add_files_button.set_size_request(300, 60)
            button_content = Adw.ButtonContent(
                label="Add Audio Files",
                icon_name="list-add-symbolic"
            )
            add_files_button.set_child(button_content)
            add_files_button.add_css_class("suggested-action")
            add_files_button.add_css_class("pill")
            add_files_button.connect("clicked", self.on_add_audio)
            drop_target = Gtk.DropTarget.new(type=Gdk.FileList, actions=Gdk.DragAction.COPY)
            drop_target.connect("drop", self._on_icon_dnd_drop, add_files_button)
            add_files_button.add_controller(drop_target)
            button_container.append(add_files_button)

            self.live_trans_button = Gtk.Button()
            self.live_trans_button.set_size_request(300, 60)
            live_button_content = Adw.ButtonContent(
                label="Start Live Transcription",
                icon_name="mic-symbolic"
            )
            self.live_trans_button.set_child(live_button_content)
            self.live_trans_button.add_css_class("suggested-action")
            self.live_trans_button.add_css_class("pill")
            self.live_trans_button.connect("clicked", self.on_live_transcription)
            button_container.append(self.live_trans_button)

            main_content_box.append(button_container)

        settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        model_box.set_margin_start(12)
        model_box.set_margin_end(12)
        model_box.set_margin_top(6)
        model_box.set_margin_bottom(6)
        model_label_key = Gtk.Label(label="Model")
        model_label_key.set_halign(Gtk.Align.START)
        model_label_key.set_hexpand(True)
        model_box.append(model_label_key)
        model_label_value = Gtk.Label(label=self._get_model_name())
        model_label_value.set_halign(Gtk.Align.START)
        model_box.append(model_label_value)
        model_settings_btn = Gtk.Button()
        model_settings_btn.set_icon_name("emblem-system-symbolic")
        model_settings_btn.set_valign(Gtk.Align.CENTER)
        model_settings_btn.add_css_class("flat")
        model_settings_btn.set_tooltip_text("Open model settings")
        model_settings_btn.connect("clicked", lambda btn: self.on_settings(None, None))
        model_box.append(model_settings_btn)
        settings_box.append(model_box)

        timestamps_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        timestamps_box.set_margin_start(12)
        timestamps_box.set_margin_end(12)
        timestamps_box.set_margin_top(6)
        timestamps_box.set_margin_bottom(6)
        timestamps_label_key = Gtk.Label(label="Timestamps")
        timestamps_label_key.set_halign(Gtk.Align.START)
        timestamps_label_key.set_hexpand(True)
        timestamps_box.append(timestamps_label_key)
        timestamps_label_value = Gtk.Label(label="Enabled" if self.ts_enabled else "Disabled")
        timestamps_label_value.set_halign(Gtk.Align.START)
        timestamps_box.append(timestamps_label_value)
        timestamps_switch = Gtk.Switch()
        timestamps_switch.set_valign(Gtk.Align.CENTER)
        timestamps_switch.set_active(self.ts_enabled)
        timestamps_switch.connect("notify::active", self._on_timestamps_toggled)
        timestamps_box.append(timestamps_switch)
        settings_box.append(timestamps_box)

        output_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        output_box.set_margin_start(12)
        output_box.set_margin_end(12)
        output_box.set_margin_top(6)
        output_box.set_margin_bottom(6)
        output_label_key = Gtk.Label(label="Output Directory")
        output_label_key.set_halign(Gtk.Align.START)
        output_label_key.set_hexpand(True)
        output_box.append(output_label_key)
        output_label_value = Gtk.Label(label=self.output_directory or "Not set")
        output_label_value.set_halign(Gtk.Align.START)
        output_box.append(output_label_value)
        output_open_btn = Gtk.Button()
        output_open_btn.set_icon_name("folder-visiting-symbolic")
        output_open_btn.set_valign(Gtk.Align.CENTER)
        output_open_btn.add_css_class("flat")
        output_open_btn.set_tooltip_text("Open output directory")
        output_open_btn.connect("clicked", self._open_output_directory)
        output_box.append(output_open_btn)
        settings_box.append(output_box)

        settings_box.add_css_class("card")
        main_content_box.append(settings_box)

        settings_clamp = Adw.Clamp()
        settings_clamp.set_child(main_content_box)
        self.status_card.set_child(settings_clamp)

    def _get_model_name(self):
        selected_index = self.model_combo.get_selected() if self.model_combo else Gtk.INVALID_LIST_POSITION
        if selected_index != Gtk.INVALID_LIST_POSITION and self.model_strings.get_n_items() > 0:
            active = self.model_strings.get_string(selected_index)
            return self.display_to_core.get(active, "None")
        return "None"

    def _open_output_directory(self, button):
        out_dir = self.output_directory or os.path.expanduser("~/Downloads")
        if os.path.isdir(out_dir):
            try:
                Gio.AppInfo.launch_default_for_uri(f"file://{out_dir}", None)
            except Exception as e:
                self._error(f"Failed to open directory: {e}")
        else:
            self._error("Output directory is not set or invalid.")

    def add_file_to_list(self, filename, file_path):
        if not self.files_group:
            raise RuntimeError("Files group not initialized.")

        file_row = Adw.ActionRow()
        file_row.set_title(filename)
        file_row.set_subtitle(os.path.dirname(file_path) or "Local File")

        remove_btn = Gtk.Button()
        remove_btn.set_icon_name("user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.add_css_class("destructive-action")
        remove_btn.set_tooltip_text("Remove file")
        file_row.add_suffix(remove_btn)
        remove_btn.connect("clicked", self._on_remove_file, file_path)

        progress_widget = Gtk.Image.new_from_icon_name("hourglass-symbolic")
        file_row.add_suffix(progress_widget)

        output_buffer = Gtk.TextBuffer()
        output_view = Gtk.TextView.new_with_buffer(output_buffer)
        output_view.set_editable(False)
        output_view.set_monospace(True)
        output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        file_data = {
            'row': file_row,
            'remove_btn': remove_btn,
            'icon': progress_widget,
            'filename': filename,
            'path': file_path,
            'status': 'waiting',
            'buffer': output_buffer,
            'view': output_view,
            'is_viewed': False
        }
        self.progress_items.append(file_data)

        file_row.set_activatable(True)
        file_row.connect('activated', lambda r: self.show_file_details(file_data))
        self.files_group.add(file_row)
        GLib.idle_add(self.update_status_card)
        return file_data

    def _on_remove_file(self, button, file_path):
        file_data = next((item for item in self.progress_items if item['path'] == file_path), None)
        if not file_data:
            return

        if file_data['status'] == 'processing' and self.current_proc and self.current_proc.poll() is None:
            dialog = Adw.AlertDialog(
                heading="Confirm File Removal",
                body=f"'{os.path.basename(file_path)}' is being transcribed. Stop transcription and remove it?",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("remove", "Stop and Remove")
            dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", lambda d, r: self._on_remove_file_response(r, file_data, file_path))
            dialog.present(self.window)
        else:
            self._remove_single_file(file_data, file_path)

    def _on_remove_file_response(self, response, file_data, file_path):
        if response == "remove":
            self.cancel_flag = True
            if self.current_proc and self.current_proc.poll() is None:
                try:
                    self.current_proc.terminate()
                    self.current_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.current_proc.kill()
            self._remove_single_file(file_data, file_path)

    def _remove_single_file(self, file_data, file_path):
        self.files_group.remove(file_data['row'])
        self.progress_items.remove(file_data)
        for i in range(self.audio_store.get_n_items() - 1, -1, -1):
            if self.audio_store.get_string(i) == file_path:
                self.audio_store.splice(i, 1, [])
                break
        if not self.progress_items and self.navigation_view.get_visible_page().get_tag() != "welcome":
            self.navigation_view.pop_to_tag("welcome")
        if file_data['is_viewed'] and self.navigation_view.get_visible_page().get_tag() == "details":
            self.navigation_view.pop()
        GLib.idle_add(self.update_status_card)

    def show_file_details(self, file_data):
        for item in self.progress_items:
            item['is_viewed'] = False
        file_data['is_viewed'] = True

        details_page = self.navigation_view.find_page("details")
        details_scrolled = details_page.get_child()
        details_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        details_content.set_margin_top(12)
        details_content.set_margin_bottom(12)
        details_content.set_margin_start(12)
        details_content.set_margin_end(12)

        status_page = Adw.StatusPage()
        status_page.set_title(file_data['filename'])

        if file_data['status'] == 'waiting':
            status_page.set_icon_name("media-playback-start-symbolic")
            status_page.set_description("Waiting to start...")
        elif file_data['status'] == 'processing':
            spinner = Gtk.Spinner()
            spinner.set_spinning(True)
            status_page.set_child(spinner)
            status_page.set_description("Transcribing...")
        elif file_data['status'] == 'completed':
            status_page.set_icon_name("emoji-body-symbolic")
            status_page.set_description("Transcription completed")
            if file_data['buffer'] and file_data['buffer'].get_char_count() > 0:
                output_box = self.create_output_widget(file_data)
                status_page.set_child(output_box)
        elif file_data['status'] == 'error':
            status_page.set_icon_name("dialog-error-symbolic")
            status_page.set_description("Error occurred during transcription")
        else:
            status_page.set_icon_name("audio-x-generic-symbolic")
            status_page.set_description(f"Location: {os.path.dirname(file_data['path'])}")

        details_content.append(status_page)
        details_content_clamp = Adw.Clamp()
        details_content_clamp.set_child(details_content)
        details_scrolled.set_child(details_content_clamp)

        if self.navigation_view.get_visible_page().get_tag() != "details":
            self.navigation_view.push(details_page)
        GLib.idle_add(self._update_navigation_buttons, "details")

    def create_output_widget(self, file_data):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_size_request(400, 300)

        text_view = Gtk.TextView.new_with_buffer(file_data['buffer'])
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_margin_start(12)
        text_view.set_margin_end(12)
        text_view.set_margin_top(12)
        text_view.set_margin_bottom(12)

        scrolled.set_child(text_view)
        scrolled.add_css_class("card")

        return scrolled

    def update_file_status(self, file_data, status, message=""):
        file_data['status'] = status
        if status == 'processing':
            spinner = Gtk.Spinner()
            spinner.set_spinning(True)
            file_data['row'].remove(file_data['icon'])
            file_data['row'].add_suffix(spinner)
            file_data['icon'] = spinner
        else:
            icon_name = {
                'waiting': 'hourglass-symbolic',
                'completed': 'emblem-ok-symbolic',
                'error': 'dialog-error-symbolic'
            }.get(status, 'hourglass-symbolic')
            if isinstance(file_data['icon'], Gtk.Spinner):
                file_data['row'].remove(file_data['icon'])
                file_data['icon'] = Gtk.Image.new_from_icon_name(icon_name)
                file_data['row'].add_suffix(file_data['icon'])
            else:
                file_data['icon'].set_from_icon_name(icon_name)
        file_data['row'].set_subtitle(message or status.title())
        if file_data['is_viewed'] and self.navigation_view.get_visible_page().get_tag() == "details":
            self.show_file_details(file_data)

    def add_log_text(self, file_data, text):
        if file_data['buffer']:
            end_iter = file_data['buffer'].get_end_iter()
            file_data['buffer'].insert(end_iter, text + "\n")
            if file_data['is_viewed'] and self.navigation_view.get_visible_page().get_tag() == "details":
                mark = file_data['buffer'].get_insert()
                file_data['view'].scroll_mark_onscreen(mark)

    def add_live_transcription_text(self, text):
        if self.live_transcription_buffer:
            end_iter = self.live_transcription_buffer.get_end_iter()
            self.live_transcription_buffer.insert(end_iter, text + "\n")
            mark = self.live_transcription_buffer.get_insert()
            self.live_transcription_view.scroll_mark_onscreen(mark)

    def on_live_transcription(self, button):
        if self.live_transcribing:
            self.stop_live_transcription()
            button.set_child(Adw.ButtonContent(label="Start Live Transcription", icon_name="mic-symbolic"))
            self._green(button)
            self.live_status_label.set_label("Live transcription stopped")
        else:
            selected_index = self.model_combo.get_selected()
            if selected_index == Gtk.INVALID_LIST_POSITION:
                self._error("No model selected in settings.")
                return
            core = self.display_to_core.get(self.model_strings.get_string(selected_index))
            if not core:
                self._error("Invalid model selection.")
                return
            model_path = self._model_target_path(core)
            if not os.path.isfile(model_path):
                self._error("Model not installed. Install it in settings.")
                return
            self.start_live_transcription(model_path, core)
            button.set_child(Adw.ButtonContent(label="Stop Live Transcription", icon_name="media-playback-stop-symbolic"))
            self._red(button)
            self.live_status_label.set_label("Live transcription running...")
            self.navigation_view.push_by_tag("live")

    def start_live_transcription(self, model_path, core):
        self.live_transcribing = True
        self.cancel_flag = False
        try:
            devices = sd.query_devices()
            GLib.idle_add(self._error, f"Available devices:\n{devices}")
            input_device = None
            for device in devices:
                if device['max_input_channels'] > 0 and 'pulse' in device['name'].lower():
                    input_device = device['index']
                    break
            if input_device is None:
                for device in devices:
                    if device['max_input_channels'] > 0 and 'default' in device['name'].lower():
                        input_device = device['index']
                        break
            if input_device is None:
                GLib.idle_add(self._error, f"No input device found. Available devices:\n{devices}")
                self.live_transcribing = False
                return
            self.audio_stream = sd.InputStream(
                callback=self._audio_callback,
                dtype='int16',
                channels=1,
                samplerate=16000,
                blocksize=16000*5,
                device=input_device
            )
            self.audio_stream.start()
            GLib.idle_add(self._error, f"Using input device: {sd.query_devices(input_device)['name']}")
            threading.Thread(target=self._process_audio, args=(model_path, core), daemon=True).start()
        except Exception as e:
            GLib.idle_add(self._error, f"Failed to start audio stream: {e}")
            self.live_transcribing = False

    def stop_live_transcription(self):
        self.live_transcribing = False
        self.cancel_flag = True
        if self.audio_stream:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
                self.audio_stream = None
            except Exception as e:
                GLib.idle_add(self._error, f"Failed to stop audio stream: {e}")
        while not self.audio_queue.empty():
            try:
                audio_file = self.audio_queue.get_nowait()
                if os.path.exists(audio_file):
                    os.remove(audio_file)
            except Exception:
                pass
        if os.path.exists(self.temp_audio_dir):
            try:
                shutil.rmtree(self.temp_audio_dir)
            except Exception:
                pass
        self.temp_audio_dir = tempfile.mkdtemp()

    def _audio_callback(self, indata, frames, time, status):
        if status:
            GLib.idle_add(self._error, f"Audio stream status: {status}")
        if not self.live_transcribing:
            return
        temp_file = os.path.join(self.temp_audio_dir, f"chunk_{datetime.now().timestamp()}.wav")
        with wave.open(temp_file, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(indata.tobytes())
        self.audio_queue.put(temp_file)

    def _process_audio(self, model_path, core):
        while self.live_transcribing or not self.audio_queue.empty():
            if self.cancel_flag:
                break
            try:
                audio_file = self.audio_queue.get(timeout=1)
                output_filename = os.path.splitext(audio_file)[0]
                cmd = [self.bin_path, "-m", model_path, "-f", audio_file]
                if not self.ts_enabled:
                    cmd.append("-nt")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    errors='replace'
                )
                transcription = ""
                for line in proc.stdout:
                    transcription += line.rstrip() + "\n"
                proc.stdout.close()
                proc.wait()
                if proc.returncode == 0:
                    GLib.idle_add(self.add_live_transcription_text, transcription)
                else:
                    error = proc.stderr.read().strip()
                    proc.stderr.close()
                    GLib.idle_add(self._error, f"Transcription error: {error}")
                os.remove(audio_file)
                self.audio_queue.task_done()
            except Exception as e:
                if not self.cancel_flag:
                    GLib.idle_add(self._error, f"Processing error: {e}")
        self.audio_queue.queue.clear()

    def on_about(self, action, param):
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name=self.title,
            application_icon="io.github.JaredTweed.AudioToTextTranscriber",
            version="1.0",
            developers=["Jared Tweed", "Mohammed Asif Ali Rizvan"],
            license_type=Gtk.License.GPL_3_0,
            comments="A GUI for whisper.cpp to transcribe audio files and live microphone input.",
            website="https://github.com/your-repo"
        )
        about.present()

    def on_toggle_timestamps(self, action, param):
        self.ts_enabled = not self.ts_enabled
        action.set_state(GLib.Variant.new_boolean(self.ts_enabled))
        GLib.idle_add(self.update_status_card)

    def _on_model_combo_changed(self, dropdown, _):
        self.save_settings()
        self._update_model_btn()
        GLib.idle_add(self.update_status_card)

    def _model_target_path(self, core):
        return os.path.join(self.models_dir, f"ggml-{core}.bin")

    def _update_model_btn(self):
        selected_index = self.model_combo.get_selected() if self.model_combo else Gtk.INVALID_LIST_POSITION
        if selected_index == Gtk.INVALID_LIST_POSITION or self.model_strings.get_n_items() == 0:
            if self.model_btn:
                self.model_btn.set_label("No Model Selected")
                self.status_lbl.set_label("No Model Selected")
            if hasattr(self, 'trans_btn'):
                self.trans_btn.set_sensitive(False)
                self.status_lbl.set_label("No Model Selected, Goto Settings!")
            return False

        active = self.model_strings.get_string(selected_index)
        if not active:
            if self.model_btn:
                self.model_btn.set_label("No Model Selected")
                self.status_lbl.set_label("No Model Selected")
            if hasattr(self, 'trans_btn'):
                self.trans_btn.set_sensitive(False)
                self.status_lbl.set_label("No Model Selected, Goto Settings!")
            return False

        if self.dl_info:
            done = os.path.getsize(self.dl_info["target"]) // MB if os.path.isfile(self.dl_info["target"]) else 0
            tot = self.dl_info["total_mb"] or "?"
            if self.model_btn:
                self.model_btn.set_label(f"Cancel Download {done} / {tot} MB")
            if hasattr(self, 'trans_btn'):
                self.trans_btn.set_sensitive(False)
                self.status_lbl.set_label("No Model Selected, Goto Settings!")
            return True

        try:
            core = self.display_to_core[active]
        except KeyError:
            if self.model_btn:
                self.model_btn.set_label("No Model Selected, Goto Settings!")
            if hasattr(self, 'trans_btn'):
                self.trans_btn.set_sensitive(False)
                self.status_lbl.set_label("No Model Selected, Goto Settings!")
            return False

        exists = os.path.isfile(self._model_target_path(core))
        if self.model_btn:
            self.model_btn.set_label("Delete Model" if exists else "Install Model")
        if hasattr(self, 'trans_btn'):
            self.trans_btn.set_sensitive(exists)
            if exists:
                self.status_lbl.set_label(f"Model: {core} in use")
            else:
                self.status_lbl.set_label(f"Model: {core}, Goto settings to download")
        return True

    def on_model_btn(self, _):
        if self.dl_info:
            self.cancel_flag = True
            proc = self.dl_info.get("proc")
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                self.dl_info["cancelled"] = True
                self.status_lbl.set_label("Cancelling download...")
                GLib.idle_add(self._on_download_done, False)
            return
        selected_index = self.model_combo.get_selected()
        if selected_index == Gtk.INVALID_LIST_POSITION:
            return
        active = self.model_strings.get_string(selected_index)
        core = self.display_to_core.get(active)
        if not core:
            return
        target = self._model_target_path(core)
        if os.path.isfile(target):
            self._yes_no(f"Delete model '{core}'?", lambda confirmed: self._on_delete_model(confirmed, target, core))
            return
        self._start_download(core)

    def _on_delete_model(self, confirmed, target, core):
        if not confirmed:
            return
        try:
            if os.path.isfile(target):
                os.remove(target)
                GLib.idle_add(self.status_lbl.set_label, f"Model deleted: {core}")
                GLib.idle_add(self._refresh_model_menu)
                GLib.idle_add(self._update_model_btn)
            else:
                GLib.idle_add(self._error, "Model file not found.")
        except Exception as e:
            GLib.idle_add(self._error, f"Failed to delete model: {str(e)}")
        GLib.idle_add(lambda: self.model_combo.notify("selected"))

    def _start_download(self, core):
        target = self._model_target_path(core)
        family = core.split(".", 1)[0].split("-")[0]
        total_mb = MODEL_SIZE_MB.get(family, None)
        self.dl_info = {"core": core, "target": target, "total_mb": total_mb, "done_mb": 0}
        self.status_lbl.set_label(f"Starting download for “{core}”...")
        self._update_model_btn()
        threading.Thread(target=self._download_model_thread, args=(core,), daemon=True).start()
        GLib.timeout_add(500, self._poll_download_progress)

    def _poll_download_progress(self):
        if not self.dl_info or self.cancel_flag:
            return False
        target = self.dl_info["target"]
        if os.path.isfile(target):
            self.dl_info["done_mb"] = os.path.getsize(target) // MB
        self._update_model_btn()
        return True

    def _download_model_thread(self, core):
        target = self._model_target_path(core)
        cmd = ["sh", self.download_script, core, self.models_dir]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            self.dl_info["proc"] = proc
            while proc.poll() is None:
                if self.cancel_flag:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    GLib.idle_add(self._on_download_done, False)
                    return
                line = proc.stdout.readline().strip()
                if line:
                    GLib.idle_add(self.status_lbl.set_label, line[:120])
            proc.stdout.close()
            proc.wait()
            GLib.idle_add(self._on_download_done, proc.returncode == 0)
        except Exception as e:
            GLib.idle_add(self._error, f"Download failed: {e}")
            GLib.idle_add(self._on_download_done, False)

    def _on_download_done(self, success):
        if not self.dl_info:
            return
        cancelled = self.dl_info.get("cancelled", False)
        target = self.dl_info["target"]
        core = self.dl_info["core"]
        if cancelled or self.cancel_flag:
            self.status_lbl.set_label(f"Download cancelled for “{core}”.")
            if os.path.isfile(target):
                try:
                    os.remove(target)
                except:
                    pass
        else:
            expected_mb = self.dl_info["total_mb"]
            actual_mb = os.path.getsize(target) // MB if os.path.isfile(target) else 0
            if not success or (expected_mb and abs(actual_mb - expected_mb) > 5):
                if os.path.isfile(target):
                    os.remove(target)
                self._error(f"Failed to download model “{core}”.")
            else:
                self.status_lbl.set_label(f"Model “{core}” installed.")
        self.dl_info = None
        self.cancel_flag = False
        self._refresh_model_menu()
        self._update_model_btn()

    def _update_download_progress(self):
        if not self.dl_info:
            return False
        proc, target = self.dl_info["proc"], self.dl_info["target"]
        if proc.poll() is None:
            GLib.idle_add(self._update_model_btn)
            return True
        success = (proc.returncode == 0 and os.path.isfile(target))
        if success:
            expected_mb = self.dl_info["total_mb"]
            actual_mb = os.path.getsize(target) // MB if os.path.isfile(target) else 0
            if expected_mb and abs(actual_mb - expected_mb) > 5:
                success = False
                GLib.idle_add(self._error, f"Model {self.dl_info['core']} size mismatch: expected {expected_mb} MB, got {actual_mb} MB")
                if os.path.isfile(target):
                    os.remove(target)
        if not success and os.path.isfile(target):
            os.remove(target)
            GLib.idle_add(self._error, f"Failed to download model “{self.dl_info['core']}”.")
        else:
            GLib.idle_add(self.status_lbl.set_label, f"Model “{self.dl_info['core']}” installed.")
        self.dl_info = None
        GLib.idle_add(self._refresh_model_menu)
        GLib.idle_add(self._update_model_btn)
        return False

    def _remote_size_bytes(self, core):
        src = "https://huggingface.co/ggerganov/whisper.cpp"
        pfx = "resolve/main/ggml"
        if "tdrz" in core:
            src = "https://huggingface.co/akashmjn/tinydiarize-whisper.cpp"
        url = f"{src}/{pfx}-{core}.bin"
        for tool in (["curl", "-sIL", url], ["wget", "--spider", "-S", url]):
            try:
                out = subprocess.check_output(tool, stderr=subprocess.STDOUT, text=True, timeout=8)
                for ln in out.splitlines():
                    if "Content-Length" in ln:
                        return int(ln.split()[-1])
            except Exception:
                continue
        return None

    def _on_audio_key(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self.on_remove_audio(None, None)
            return True
        if keyval in (Gdk.KEY_a, Gdk.KEY_A) and state & Gdk.ModifierType.CONTROL_MASK:
            return True
        return False

    def on_add_audio(self, _):
        choice_dialog = Adw.AlertDialog(
            heading="Add Audio",
            body="What would you like to add?"
        )
        choice_dialog.add_response("cancel", "Cancel")
        choice_dialog.add_response("folders", "Select Folders")
        choice_dialog.add_response("files", "Select Files")
        choice_dialog.set_response_appearance("files", Adw.ResponseAppearance.SUGGESTED)
        choice_dialog.connect("response", self._on_add_choice_response)
        choice_dialog.present(self.window)

    def _on_add_choice_response(self, dialog, response):
        if response == "files":
            self._select_audio_files()
        elif response == "folders":
            self._select_audio_folders()

    def _select_audio_files(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select audio files")
        dialog.set_accept_label("Add")
        f = Gtk.FileFilter()
        f.set_name("Audio Files")
        for ext in ("*.mp3", "*.wav", "*.flac", "*.m4a", "*.ogg", "*.opus"):
            f.add_pattern(ext)
        filters = Gio.ListStore()
        filters.append(f)
        dialog.set_filters(filters)
        dialog.open_multiple(self.window, None, self._on_add_files_response)

    def _select_audio_folders(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select folders containing audio files")
        dialog.set_accept_label("Add")
        dialog.select_multiple_folders(self.window, None, self._on_add_folders_response)

    def _on_add_files_response(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
            new_paths = self._collect_audio_files(files)
            for fn in new_paths:
                if fn not in [self.audio_store.get_string(i) for i in range(self.audio_store.get_n_items())]:
                    self.audio_store.append(fn)
                    self.add_file_to_list(os.path.basename(fn), fn)
            if new_paths:
                toast = Adw.Toast(title=f"Added {len(new_paths)} file(s)")
                toast.set_timeout(3)
                self.toast_overlay.add_toast(toast)
                GLib.idle_add(self.navigation_view.push_by_tag, "review")
        except GLib.Error:
            pass

    def _on_add_folders_response(self, dialog, result):
        try:
            folders = dialog.select_multiple_folders_finish(result)
            new_paths = self._collect_audio_files(folders)
            for fn in new_paths:
                if fn not in [self.audio_store.get_string(i) for i in range(self.audio_store.get_n_items())]:
                    self.audio_store.append(fn)
                    self.add_file_to_list(os.path.basename(fn), fn)
            if new_paths:
                toast = Adw.Toast(title=f"Added {len(new_paths)} file(s)")
                toast.set_timeout(3)
                self.toast_overlay.add_toast(toast)
                GLib.idle_add(self.navigation_view.push_by_tag, "review")
        except GLib.Error:
            pass

    def _collect_audio_files(self, files):
        audio_ext = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus")
        found = []
        seen = set(self.audio_store.get_string(i) for i in range(self.audio_store.get_n_items()))
        def _add_if_ok(p):
            path = p.get_path() if isinstance(p, Gio.File) else p
            if path and path.lower().endswith(audio_ext) and path not in seen:
                found.append(path)
                seen.add(path)
        for p in files:
            path = p.get_path() if isinstance(p, Gio.File) else p
            if os.path.isfile(path):
                _add_if_ok(path)
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for f in files:
                        _add_if_ok(os.path.join(root, f))
        return found

    def on_remove_audio(self, action, param):
        if not self.progress_items:
            return
        if self.current_proc and self.current_proc.poll() is None:
            dialog = Adw.AlertDialog(
                heading="Confirm Removal of All Files",
                body="A transcription is currently in progress. Do you want to stop the transcription and remove all audio files?"
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("remove", "Stop and Remove All")
            dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", lambda d, r: self._on_remove_all_response(r))
            dialog.present(self.window)
        else:
            self._remove_all_files()

    def _on_remove_all_response(self, response):
        if response == "remove":
            self.cancel_flag = True
            if self.current_proc and self.current_proc.poll() is None:
                try:
                    self.current_proc.terminate()
                    self.current_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.current_proc.kill()
            self._remove_all_files()
            GLib.idle_add(self.update_status_card, "cancelled")

    def _remove_all_files(self):
        for file_data in self.progress_items:
            self.files_group.remove(file_data['row'])
        self.progress_items.clear()
        self.audio_store.splice(0, self.audio_store.get_n_items(), [])
        if self.navigation_view.get_visible_page().get_tag() != "welcome":
            self.navigation_view.pop_to_tag("welcome")
        GLib.idle_add(self.update_status_card)

    def _browse_out_settings(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Output Directory")
        parent = getattr(self, 'settings_dialog', None) or self.window

        def on_folder_selected(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
                if folder:
                    path = folder.get_path()
                    self.output_settings_row.set_subtitle(path)
                    self._save_output_directory(path)
                    GLib.idle_add(self.update_status_card)
            except Exception as e:
                print(f"Error selecting folder: {e}")

        dialog.select_folder(parent, None, on_folder_selected)

    def _save_output_directory(self, path):
        self.output_directory = path
        self.save_settings()

    def _setup_dnd(self):
        target = Gtk.DropTarget.new(type=Gdk.FileList, actions=Gdk.DragAction.COPY)
        target.connect("drop", self._on_dnd_drop)
        self.files_group.add_controller(target)

    def _on_dnd_drop(self, drop_target, value, x, y):
        files = value.get_files()
        new_paths = self._collect_audio_files(files)
        for path in new_paths:
            if path not in [self.audio_store.get_string(i) for i in range(self.audio_store.get_n_items())]:
                self.audio_store.append(path)
                self.add_file_to_list(os.path.basename(path), path)
        if new_paths:
            toast = Adw.Toast(title=f"Added {len(new_paths)} file(s)")
            toast.set_timeout(3)
            self.toast_overlay.add_toast(toast)
        return True

    def on_transcribe(self, _):
        if self.trans_btn.get_label() == "Cancel":
            self.cancel_flag = True
            if self.current_proc:
                try:
                    self.current_proc.terminate()
                except:
                    pass
            self._gui_status("Cancelling...")
            GLib.idle_add(self.update_status_card, "cancelled")
            return

        selected_index = self.model_combo.get_selected()
        if selected_index == Gtk.INVALID_LIST_POSITION:
            self._error("No model selected in settings.")
            return

        core = self.display_to_core.get(self.model_strings.get_string(selected_index))
        if not core:
            self._error("Invalid model selection.")
            return

        model_path = self._model_target_path(core)
        if not os.path.isfile(model_path):
            self._error("Model not installed. Install it in settings.")
            return

        files = [self.audio_store.get_string(i) for i in range(self.audio_store.get_n_items())]
        out_dir = getattr(self, 'output_directory', None) or os.path.expanduser("~/Downloads")

        if not files:
            self._error("No audio files selected.")
            return

        if not out_dir or not os.path.isdir(out_dir):
            self._error("Choose a valid output folder in settings.")
            return

        if not os.path.isfile(self.bin_path):
            self.trans_btn.set_sensitive(False)
            self.model_btn.set_label("No Model Selected, Goto Settings!")
            def _build_and_continue():
                success = self._ensure_whisper_cli()
                if success:
                    GLib.idle_add(self.on_transcribe, None)
                GLib.idle_add(self.trans_btn.set_sensitive, True)
            threading.Thread(target=_build_and_continue, daemon=True).start()
            return

        self.cancel_flag = False
        self.trans_btn.set_label("Cancel")
        self._red(self.trans_btn)
        GLib.idle_add(self.update_status_card, "started")
        if self.navigation_view.get_visible_page().get_tag() != "review":
            GLib.idle_add(self.navigation_view.push_by_tag, "review")
        threading.Thread(target=self._worker, args=(model_path, files, out_dir, core), daemon=True).start()

    def _worker(self, model_path, files, out_dir, core):
        total = len(files)
        idx = 1
        while idx <= total:
            if self.cancel_flag:
                self.cancel_flag = False
                idx += 1
                continue
            file_path = self.audio_store.get_string(idx - 1) if idx - 1 < self.audio_store.get_n_items() else None
            if not file_path:
                idx += 1
                continue
            filename = os.path.basename(file_path)
            self._gui_status(f"{idx}/{total} – {filename}")

            file_data = next((item for item in self.progress_items if item['path'] == file_path), None)
            if not file_data or 'buffer' not in file_data or not file_data['buffer']:
                GLib.idle_add(self._error, f"Invalid or missing file_data for {filename}")
                idx += 1
                continue

            GLib.idle_add(self.update_file_status, file_data, 'processing', f"Transcribing ({idx}/{total})...")

            cmd = [self.bin_path, "-m", model_path, "-f", file_path]
            if not self.ts_enabled:
                cmd.append("-nt")

            self.current_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                errors='replace'
            )

            for line in self.current_proc.stdout:
                if self.cancel_flag:
                    try:
                        self.current_proc.terminate()
                    except:
                        pass
                    GLib.idle_add(self.update_file_status, file_data, 'error', "Cancelled")
                    GLib.idle_add(self.add_log_text, file_data, "Transcription cancelled")
                    break
                GLib.idle_add(self.add_log_text, file_data, line.rstrip())

            self.current_proc.stdout.close()
            self.current_proc.wait()

            if self.cancel_flag:
                GLib.idle_add(self.update_file_status, file_data, 'error', "Cancelled")
            else:
                if self.current_proc.returncode != 0:
                    error = self.current_proc.stderr.read().strip()
                    self.current_proc.stderr.close()
                    GLib.idle_add(self.update_file_status, file_data, 'error', f"Error occurred")
                    GLib.idle_add(self.add_log_text, file_data, f"ERROR: {error}")
                    GLib.idle_add(self.update_status_card, "error")
                else:
                    self.current_proc.stderr.close()
                    dest = os.path.join(out_dir, os.path.splitext(filename)[0] + ".txt")
                    def _save():
                        if file_data and 'buffer' in file_data and file_data['buffer']:
                            txt = file_data['buffer'].get_text(
                                file_data['buffer'].get_start_iter(),
                                file_data['buffer'].get_end_iter(),
                                False
                            )
                            with open(dest, "w", encoding="utf-8") as f:
                                f.write(txt)
                        return False
                    GLib.idle_add(_save)
                    GLib.idle_add(self.update_file_status, file_data, 'completed', "Completed successfully")
            idx += 1

        if self.cancel_flag:
            self._gui_status("Cancelled")
            GLib.idle_add(self.update_status_card, "cancelled")
        else:
            self._gui_status("Done")
            GLib.idle_add(self.update_status_card, "completed")
        GLib.idle_add(self._reset_btn)

    def _ensure_whisper_cli(self):
        if shutil.which("whisper-cli"):
            self.bin_path = shutil.which("whisper-cli")
            return True
        if os.path.isfile(self.bin_path):
            return True
        GLib.idle_add(self.status_lbl.set_label, "Building whisper-cli (~2 min)...")
        try:
            res1 = subprocess.run(
                ["cmake", "-B", "build"],
                cwd=self.repo_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors='replace'
            )
            if res1.returncode != 0:
                raise RuntimeError(res1.stderr)
            res2 = subprocess.run(
                ["cmake", "--build", "build", "--config", "Release"],
                cwd=self.repo_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors='replace'
            )
            if res2.returncode != 0:
                raise RuntimeError(res2.stderr)
            if not os.path.isfile(self.bin_path):
                raise FileNotFoundError(f"{self.bin_path} still missing after build")
        except Exception as e:
            GLib.idle_add(self._error, f"Build failed:\n{e}\n{self.repo_dir}")
            return False
        GLib.idle_add(self.status_lbl.set_label, "Build complete.")
        return True

    def _green(self, b):
        b.add_css_class("suggested-action")
        b.remove_css_class("destructive-action")

    def _red(self, b):
        b.add_css_class("destructive-action")
        b.remove_css_class("suggested-action")

    def _gui_status(self, msg):
        if msg == "Idle":
            selected_index = self.model_combo.get_selected()
            if selected_index != Gtk.INVALID_LIST_POSITION:
                active = self.model_strings.get_string(selected_index)
                core = self.display_to_core.get(active, "None")
                msg = f"Idle, Model: {core}"
        GLib.idle_add(self.status_lbl.set_label, msg)

    def _reset_btn(self):
        self.trans_btn.set_label("Transcribe")
        self._green(self.trans_btn)

    def _yes_no(self, msg, callback):
        parent = getattr(self, 'settings_dialog', None) or self.window
        dialog = Adw.AlertDialog(
            heading="Confirmation",
            body=msg
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: callback(r == "ok"))
        dialog.present(parent)

    def _error(self, msg):
        parent = getattr(self, 'settings_dialog', None) or self.window
        toast = Adw.Toast(title="Error", button_label="Close")
        toast.set_timeout(5)
        toast.connect("dismissed", lambda t: None)
        self.toast_overlay.add_toast(toast)

    def _on_theme_changed(self, combo_row, _):
        self.theme_index = combo_row.get_selected()
        self.save_settings()
        style_manager = Adw.StyleManager.get_default()
        if self.theme_index == 0:
            style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        elif self.theme_index == 1:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif self.theme_index == 2:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

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
        self.output_settings_row.set_subtitle(self.output_directory)
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

    def _build_ui(self):
        self.toast_overlay = Adw.ToastOverlay()
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        main_box.set_margin_start(8)
        main_box.set_margin_end(8)
        main_box.set_margin_top(8)
        main_box.set_margin_bottom(8)
        self.toast_overlay.set_child(main_box)
        self.window.set_content(self.toast_overlay)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data("""
            listview > row:selected {
                background-color: transparent;
                color: inherit;
            }
            spinner {
                -gtk-icon-size: 16px;
            }
        """.encode('utf-8'))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.header_bar = Adw.HeaderBar()
        self.header_bar.add_css_class("flat")
        main_box.append(self.header_bar)

        self.revert_button = Gtk.Button()
        self.revert_button.set_child(Gtk.Image.new_from_icon_name("edit-undo-symbolic"))
        self.revert_button.add_css_class("flat")
        self.revert_button.set_tooltip_text("Return to Home")
        self.revert_button.connect("clicked", lambda btn: self.revert_back())
        self.header_bar.pack_start(self.revert_button)
        self.revert_button.set_visible(False)

        self.back_button = Gtk.Button()
        self.back_button.set_label("< Back")
        self.back_button.add_css_class("flat")
        self.back_button.set_tooltip_text("Back")
        self.back_button.set_visible(False)
        self.back_button.connect("clicked", lambda btn: self._on_back_clicked())
        self.header_bar.pack_start(self.back_button)

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_label = Gtk.Label(label=self.title)
        title_label.set_markup(f"<b>{self.title}</b>")
        title_box.append(title_label)
        self.header_bar.set_title_widget(title_box)

        menu_button = Gtk.MenuButton()
        menu_button.set_child(Gtk.Image.new_from_icon_name("open-menu-symbolic"))
        menu_button.add_css_class("flat")
        menu_button.set_tooltip_text("Menu")
        self.header_bar.pack_end(menu_button)

        menu = Gio.Menu()
        menu.append("Timestamps", "app.toggle-timestamps")
        menu.append("Remove All Audio", "app.remove-all-audio")
        menu.append("Settings", "app.settings")
        menu.append("About", "app.about")
        menu_button.set_menu_model(menu)

        self.create_action("about", self.on_about)
        self.create_action("settings", self.on_settings)
        self.create_action("remove-all-audio", self.on_remove_audio)
        toggle_timestamps_action = Gio.SimpleAction.new_stateful(
            "toggle-timestamps", None, GLib.Variant.new_boolean(self.ts_enabled)
        )
        toggle_timestamps_action.connect("activate", self.on_toggle_timestamps)
        self.add_action(toggle_timestamps_action)

        self.model_strings = Gtk.StringList()
        self.display_to_core = {}
        self.model_combo = Adw.ComboRow()
        self.model_combo.set_model(self.model_strings)
        self.model_combo.connect("notify::selected", self._on_model_combo_changed)
        self.model_btn = None

        self.create_navigation_ui(main_box)

        self.status_lbl = Gtk.Label(label="Idle")
        self.status_lbl.set_halign(Gtk.Align.START)
        main_box.append(self.status_lbl)

        self._refresh_model_menu()
        self._update_model_btn()

    def revert_back(self):
        current_tag = self.navigation_view.get_visible_page().get_tag()
        if current_tag == "review":
            dialog = Adw.AlertDialog(
                heading="Cancel and Remove Files?",
                body="Do you want to cancel and remove all files?",
            )
            dialog.add_response("no", "No, Continue")
            dialog.add_response("yes", "Yes, Cancel and Remove All")
            dialog.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", self._on_cancel_transcription_response)
            dialog.present(self.window)
        elif current_tag in ("details", "live"):
            self.navigation_view.pop()

    def _on_cancel_transcription_response(self, dialog, response):
        if response == "yes":
            is_transcribing = any(item['status'] == 'processing' for item in self.progress_items)
            if is_transcribing:
                self.cancel_flag = True
                if self.current_proc and self.current_proc.poll() is None:
                    try:
                        self.current_proc.terminate()
                        self.current_proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.current_proc.kill()
            self._remove_all_files_and_revert_back()
        # If response is "no", stay on review page

    def _remove_all_files_and_revert_back(self):
        self._remove_all_files()
        self.status_lbl.set_text("Files Removed")
        self.navigation_view.pop_to_tag("welcome")

    def _on_back_clicked(self):
        current_tag = self.navigation_view.get_visible_page().get_tag()
        if current_tag == "review":
            self.navigation_view.pop_to_tag("welcome")
        elif current_tag in ("details", "live"):
            if current_tag == "live" and self.live_transcribing:
                dialog = Adw.AlertDialog(
                    heading="Stop Live Transcription?",
                    body="Live transcription is running. Stop it and return?",
                )
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("stop", "Stop and Return")
                dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
                dialog.connect("response", lambda d, r: self._on_stop_live_response(r))
                dialog.present(self.window)
            else:
                self.navigation_view.pop()

    def _on_stop_live_response(self, response):
        if response == "stop":
            self.stop_live_transcription()
            if self.live_trans_button:
                self.live_trans_button.set_child(Adw.ButtonContent(label="Start Live Transcription", icon_name="mic-symbolic"))
                self._green(self.live_trans_button)
            self.live_status_label.set_label("Live transcription stopped")
            self.navigation_view.pop()

    def _on_next_clicked(self):
        current_tag = self.navigation_view.get_visible_page().get_tag()
        if current_tag == "welcome":
            self.navigation_view.push_by_tag("review")
        elif current_tag == "details":
            current_file = next((item for item in self.progress_items if item['is_viewed']), None)
            if current_file:
                current_index = self.progress_items.index(current_file)
                if current_index < len(self.progress_items) - 1:
                    next_file = self.progress_items[current_index + 1]
                    self.show_file_details(next_file)

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
            GLib.idle_add(self.update_status_card)
        except Exception as e:
            self._error(f"Error saving settings: {e}")

    def _on_timestamps_toggled(self, switch, _):
        self.ts_enabled = switch.get_active()
        self.save_settings()
        action = self.lookup_action("toggle-timestamps")
        if action:
            action.set_state(GLib.Variant.new_boolean(self.ts_enabled))
        GLib.idle_add(self.update_status_card)

    def _refresh_model_menu(self):
        current_core = None
        selected_index = self.model_combo.get_selected()
        if selected_index != Gtk.INVALID_LIST_POSITION and selected_index < self.model_strings.get_n_items():
            try:
                current_display = self.model_strings.get_string(selected_index)
                current_core = self.display_to_core.get(current_display)
            except (KeyError, IndexError):
                pass
        self.model_strings.splice(0, self.model_strings.get_n_items(), [])
        self.display_to_core.clear()
        size = {"tiny": "Smallest", "base": "Smaller", "small": "Small",
                "medium": "Medium", "large": "Large"}
        lang = {"en": "English", "fr": "French", "es": "Spanish", "de": "German"}
        selected_index = 0
        for i, core in enumerate(self.desired_models):
            size_key, lang_key = (core.split(".", 1) + [None])[:2]
            label = f"{size.get(size_key, size_key.title())} {lang.get(lang_key, lang_key.upper())}" if lang_key else size.get(size_key, size_key.title())
            if not os.path.isfile(self._model_target_path(core)):
                label += " (download)"
            self.model_strings.append(label)
            self.display_to_core[label] = core
            if core == current_core:
                selected_index = i
        if self.model_strings.get_n_items() > 0:
            self.model_combo.set_selected(selected_index)
        else:
            self.model_combo.set_selected(Gtk.INVALID_LIST_POSITION)
        GLib.idle_add(self._update_model_btn)

        if self.selected_model:
            for i in range(self.model_strings.get_n_items()):
                display = self.model_strings.get_string(i)
                if self.display_to_core.get(display) == self.selected_model:
                    self.model_combo.set_selected(i)
                    break
        self.save_settings()

if __name__ == "__main__":
    app = WhisperApp()
    app.run()
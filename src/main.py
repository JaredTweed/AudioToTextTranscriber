import gi
import os
import subprocess
import threading
import yaml
import shutil
from pathlib import Path
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
        self.add_more_button = None
        self.connect('startup', self.do_startup)
        self.connect('activate', self.do_activate)
        self.create_action("settings", self.on_settings)
        self.setup_transcripts_listbox()  # Initialize transcripts listbox

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

        self.add_more_button = Gtk.Button(label="Add Audio Files")
        self.add_more_button.connect("clicked", self.on_add_audio)
        review_content.append(self.add_more_button)

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

        # File Content Page
        content_page = Adw.NavigationPage()
        content_page.set_title("File Content")
        content_page.set_tag("file_content")
        content_scrolled = Gtk.ScrolledWindow()
        content_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        content_scrolled.set_vexpand(True)
        content_scrolled.set_hexpand(True)
        content_page.set_child(content_scrolled)
        self.navigation_view.add(content_page)

        # Transcripts Page
        transcripts_page = Adw.NavigationPage()
        transcripts_page.set_title("Transcripts")
        transcripts_page.set_tag("transcripts")
        transcripts_scrolled = Gtk.ScrolledWindow()
        transcripts_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        transcripts_scrolled.set_vexpand(True)
        transcripts_scrolled.set_hexpand(True)

        transcripts_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        transcripts_content.set_margin_start(12)
        transcripts_content.set_margin_end(12)
        transcripts_content.set_margin_top(6)
        transcripts_content.set_margin_bottom(6)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search in transcripts...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        transcripts_content.append(self.search_entry)

        self.transcripts_group = Gtk.ListBox()
        self.transcripts_group.set_selection_mode(Gtk.SelectionMode.NONE)
        self.transcripts_group.add_css_class("boxed-list")
        self.transcripts_group.set_margin_start(12)
        self.transcripts_group.set_margin_end(12)
        transcripts_content.append(self.transcripts_group)

        transcripts_content_clamp = Adw.Clamp()
        transcripts_content_clamp.set_child(transcripts_content)
        transcripts_scrolled.set_child(transcripts_content_clamp)
        transcripts_page.set_child(transcripts_scrolled)
        self.navigation_view.add(transcripts_page)

        self.navigation_view.connect("pushed", self._on_navigation_pushed)
        self.navigation_view.connect("popped", self._on_navigation_popped)

        main_box.append(self.navigation_view)
        return self.navigation_view

    def _on_navigation_pushed(self, navigation_view, *args):
        tag = navigation_view.get_visible_page().get_tag()
        self._update_navigation_buttons(tag)
        if tag == "transcripts":
            self._update_transcripts_list("")

    def _on_navigation_popped(self, navigation_view, *args):
        tag = navigation_view.get_visible_page().get_tag()
        self._update_navigation_buttons(tag)
        if tag == "transcripts":
            self._update_transcripts_list(self.search_entry.get_text().strip())

    def _update_navigation_buttons(self, tag):
        self.revert_button.set_visible(tag in ("review", "transcripts"))
        self.back_button.set_visible(tag in ("details", "transcript_details", "file_content"))
        if self.back_button.get_visible():
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
            elif transcription_status == "skipped":
                self.status_card.set_title("Files Skipped")
                self.status_card.set_icon_name("dialog-information-symbolic")
                description = "Some files were skipped due to existing transcriptions."
        else:
            self.status_card.set_title("Audio-To-Text Transcriber")
            self.status_card.set_icon_name("audio-x-generic-symbolic")
            description = "Select audio files or folders to transcribe."

        self.status_card.set_description(description)

        main_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_content_box.set_margin_top(12)
        main_content_box.set_margin_bottom(12)

        if not transcription_status:
            button_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
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

            transcripts_button = Gtk.Button()
            transcripts_button.set_size_request(300, 60)
            transcripts_content = Adw.ButtonContent(
                label="Open Transcripts",
                icon_name="document-open-symbolic"
            )
            transcripts_button.set_child(transcripts_content)
            transcripts_button.add_css_class("suggested-action")
            transcripts_button.add_css_class("pill")
            transcripts_button.connect("clicked", lambda btn: self.navigation_view.push_by_tag("transcripts"))

            out_dir = self.output_directory or os.path.expanduser("~/Downloads")
            has_txt_files = False
            if os.path.isdir(out_dir):
                for root, _, files in os.walk(out_dir):
                    if any(file.endswith(".txt") for file in files):
                        has_txt_files = True
                        break
            transcripts_button.set_sensitive(has_txt_files)

            button_container.append(transcripts_button)
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
        model_label_value = Gtk.Label(label=self._display_name(self._get_model_name()))
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

        # 1) First add the status icon (nearest the text)
        progress_widget = Gtk.Image()
        file_row.add_suffix(progress_widget)

        # 2) Then add the trash button (furthest right)
        remove_btn = Gtk.Button()
        remove_btn.set_icon_name("user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.add_css_class("destructive-action")
        remove_btn.set_tooltip_text("Remove file")
        file_row.add_suffix(remove_btn)
        remove_btn.connect("clicked", self._on_remove_file, file_path)

        # progress_widget = Gtk.Image.new_from_icon_name("hourglass-symbolic")
        # file_row.add_suffix(progress_widget)

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
        file_row.connect('activated', lambda r: self._show_file_content(file_data) if file_data['status'] == 'completed' else self.show_file_details(file_data))
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
        if file_data['is_viewed'] and self.navigation_view.get_visible_page().get_tag() in ("details", "file_content"):
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
            status_page.set_icon_name("checkbox-checked-symbolic")
            status_page.set_description("Transcription completed")
            if file_data['buffer'] and file_data['buffer'].get_char_count() > 0:
                output_box = self.create_output_widget(file_data)
                status_page.set_child(output_box)
        elif file_data['status'] == 'error':
            status_page.set_icon_name("dialog-error-symbolic")
            status_page.set_description("Error occurred during transcription")
        elif file_data['status'] == 'skipped':
            status_page.set_icon_name("dialog-information-symbolic")
            status_page.set_description("Transcription skipped due to existing file")
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

    def _show_file_content(self, file_data):
        for item in self.progress_items:
            item['is_viewed'] = False
        file_data['is_viewed'] = True

        content_page = self.navigation_view.find_page("file_content")
        content_scrolled = content_page.get_child()
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)

        status_page = Adw.StatusPage()
        status_page.set_title(file_data['filename'])
        status_page.set_icon_name("text-x-generic-symbolic")

        if file_data['buffer'] and file_data['buffer'].get_char_count() > 0:
            buffer = Gtk.TextBuffer()
            text = file_data['buffer'].get_text(
                file_data['buffer'].get_start_iter(),
                file_data['buffer'].get_end_iter(),
                False
            )
            lines = text.splitlines()
            text_with_numbers = ""
            for i, line in enumerate(lines, 1):
                text_with_numbers += f"{i:4d} | {line}\n"
            buffer.set_text(text_with_numbers)

            text_view = Gtk.TextView.new_with_buffer(buffer)
            text_view.set_editable(False)
            text_view.set_monospace(True)
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_view.set_margin_start(12)
            text_view.set_margin_end(12)
            text_view.set_margin_top(12)
            text_view.set_margin_bottom(12)

            scrolled_view = Gtk.ScrolledWindow()
            scrolled_view.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_view.set_vexpand(True)
            scrolled_view.set_hexpand(True)
            scrolled_view.set_size_request(400, 300)
            scrolled_view.set_child(text_view)
            scrolled_view.add_css_class("card")

            open_btn = Gtk.Button()
            open_btn.set_icon_name("folder-open-symbolic")
            open_btn.set_valign(Gtk.Align.CENTER)
            open_btn.add_css_class("flat")
            open_btn.set_tooltip_text("Open transcript in default editor")
            open_btn.connect("clicked", lambda btn: self._open_transcript_file(file_data['path']))

            status_page.set_child(scrolled_view)
            content_box.append(status_page)
            content_box.append(open_btn)
        else:
            status_page.set_description("No transcription content available.")

        content_clamp = Adw.Clamp()
        content_clamp.set_child(content_box)
        content_scrolled.set_child(content_clamp)

        if self.navigation_view.get_visible_page().get_tag() != "file_content":
            self.navigation_view.push(content_page)
        GLib.idle_add(self._update_navigation_buttons, "file_content")

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
        row, old_icon, remove_btn = file_data['row'], file_data['icon'], file_data['remove_btn']

        # ---- safely remove the previous status widget ----
        if old_icon and old_icon.get_parent():          # only if it’s still packed
            row.remove(old_icon)

        # ---- build the new status widget ----
        if status == 'processing':
            new_icon = Gtk.Spinner()
            new_icon.set_spinning(True)
        else:
            icon_name = {
                'waiting':   'hourglass-symbolic',
                'completed': None,                       # show nothing
                'cancelled': 'process-stop-symbolic',
                'error':     'dialog-error-symbolic',
                'skipped':   'dialog-information-symbolic',
            }.get(status, 'hourglass-symbolic')

            # None → create an empty Gtk.Image so we still have a widget to pack
            new_icon = Gtk.Image.new_from_icon_name(icon_name)

        # ---- keep the [status] [trash] order ----
        if remove_btn.get_parent():
            row.remove(remove_btn)
        row.add_suffix(new_icon)
        row.add_suffix(remove_btn)

        # ---- update bookkeeping ----
        file_data['icon']   = new_icon
        file_data['status'] = status
        row.set_subtitle(message or status.title())

        # ---- refresh Details/File-content page if it’s open ----
        if file_data['is_viewed'] and \
        self.navigation_view.get_visible_page().get_tag() in ("details", "file_content"):
            if status == 'completed':
                GLib.idle_add(self._show_file_content, file_data)
            else:
                GLib.idle_add(self.show_file_details, file_data)

    def add_log_text(self, file_data, text):
        if file_data['buffer']:
            end_iter = file_data['buffer'].get_end_iter()
            file_data['buffer'].insert(end_iter, text + "\n")
            if file_data['is_viewed'] and self.navigation_view.get_visible_page().get_tag() in ("details", "file_content"):
                mark = file_data['buffer'].get_insert()
                file_data['view'].scroll_mark_onscreen(mark)

    def on_about(self, action, param):
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name=self.title,
            application_icon="io.github.JaredTweed.AudioToTextTranscriber",
            version="1.0",
            developers=["Jared Tweed", "Mohammed Asif Ali Rizvan"],
            license_type=Gtk.License.GPL_3_0,
            comments="A GUI for whisper.cpp to transcribe audio files.",
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

    def _display_name(self, core: str) -> str:
        """Return the human-readable label that corresponds to *core*.
        Falls back to the core string itself if no match is found."""
        return next((
            label for label, c in self.display_to_core.items() if c == core
        ), core)

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
            self.trans_btn.set_sensitive(exists and not self.dl_info)
            name = self._display_name(core)
            if exists:
                self.status_lbl.set_label(f"Model: {name}, Destination: {self.output_directory or 'Not set'}")
            else:
                self.status_lbl.set_label(f"Model: {name}, Go to settings to download")
        return exists

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
        name = self._display_name(core)
        if os.path.isfile(target):
            self._yes_no(f"Delete model '{name}'?", lambda confirmed: self._on_delete_model(confirmed, target, core))
            return
        self._start_download(core)

    def _on_delete_model(self, confirmed, target, core):
        if not confirmed:
            return
        try:
            if os.path.isfile(target):
                os.remove(target)
                name = self._display_name(core)
                GLib.idle_add(self.status_lbl.set_label, f"Model deleted: {name}")
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
        name = self._display_name(core)
        self.status_lbl.set_label(f"Starting download for “{name}”...")
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
        name = self._display_name(core)
        if cancelled or self.cancel_flag:
            self.status_lbl.set_label(f"Download cancelled for “{name}”.")
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
                self._error(f"Failed to download model “{name}”.")
            else:
                self.status_lbl.set_label(f"Model “{name}” installed.")
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
            dialog.connect("response", lambda d, r: self._on_remove_all_response(d, r))
            dialog.present(self.window)
        else:
            self._remove_all_files()

    def _on_remove_all_response(self, dialog, response):
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

        dialog.select_folder(self.window, None, on_folder_selected)

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

        # Check for conflicting files
        conflicting_files = []
        non_conflicting_files = []
        for file_path in files:
            filename = os.path.basename(file_path)

            # dest = os.path.join(out_dir, filename + ".txt")
            dest = os.path.join(out_dir, os.path.splitext(filename)[0] + ".txt")
            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                conflicting_files.append(file_path)
            else:
                non_conflicting_files.append(file_path)

        if conflicting_files:
            dialog = Adw.AlertDialog(
                heading="Existing Transcriptions",
                body=f"There are {len(conflicting_files)} files with existing non-empty transcriptions. What do you want to do?"
            )
            dialog.add_response("overwrite", "Overwrite All")
            dialog.add_response("skip", "Skip Conflicting")
            dialog.add_response("cancel", "Cancel")
            dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", lambda d, r: self._on_conflict_response(r, conflicting_files, non_conflicting_files, model_path, out_dir, core))
            dialog.present(self.window)
        else:
            self._start_transcription(files, model_path, out_dir, core)

    def _on_conflict_response(self, response, conflicting_files, non_conflicting_files, model_path, out_dir, core):
        if response == "overwrite":
            files_to_transcribe = non_conflicting_files + conflicting_files
            self._start_transcription(files_to_transcribe, model_path, out_dir, core)
        elif response == "skip":
            for file_path in conflicting_files:
                file_data = next((item for item in self.progress_items if item['path'] == file_path), None)
                if file_data:
                    GLib.idle_add(self.update_file_status, file_data, 'skipped', "Skipped due to existing transcription")
            self._start_transcription(non_conflicting_files, model_path, out_dir, core)
        # Cancel does nothing

    def _start_transcription(self, files, model_path, out_dir, core):
        self.cancel_flag = False
        self.trans_btn.set_label("Cancel")
        self._red(self.trans_btn)
        GLib.idle_add(self.add_more_button.set_visible, False)
        GLib.idle_add(self.update_status_card, "started")
        if self.navigation_view.get_visible_page().get_tag() != "review":
            GLib.idle_add(self.navigation_view.push_by_tag, "review")
        threading.Thread(target=self._worker, args=(model_path, files, out_dir, core), daemon=True).start()

    def _worker(self, model_path, files, out_dir, core):
        total = len(files)
        for idx, file_path in enumerate(files, 1):
            if self.cancel_flag:
                continue
            filename = os.path.basename(file_path)
            self._gui_status(f"{idx}/{total} – {filename}")

            file_data = next((item for item in self.progress_items if item['path'] == file_path), None)
            if not file_data or 'buffer' not in file_data or not file_data['buffer']:
                GLib.idle_add(self._error, f"Invalid or missing file_data for {filename}")
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
                    GLib.idle_add(self.update_status_card)

        if self.cancel_flag:
            self._gui_status("Cancelled")
            GLib.idle_add(self.update_status_card, "cancelled")
            GLib.idle_add(self._reset_btn)
        else:
            self._gui_status("Done")
            GLib.idle_add(self.update_status_card, "completed")
            GLib.idle_add(self.trans_btn.set_label, "Transcription Complete")
            GLib.idle_add(self.trans_btn.set_sensitive, False)
            GLib.idle_add(self.add_more_button.set_label, "View Transcriptions")
            GLib.idle_add(self.add_more_button.set_visible, True)
            GLib.idle_add(self.add_more_button.disconnect_by_func, self.on_add_audio)
            GLib.idle_add(self.add_more_button.connect, "clicked", lambda btn: self.navigation_view.push_by_tag("transcripts"))

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
                name = self._display_name(core)
                msg = f"Idle, Model: {name}"
        GLib.idle_add(self.status_lbl.set_label, msg)

    def _reset_btn(self):
        self.trans_btn.set_label("Transcribe")
        self._green(self.trans_btn)
        self.trans_btn.set_sensitive(self._update_model_btn())
        if self.add_more_button:
            self.add_more_button.set_label("Add Audio Files")
            self.add_more_button.set_visible(True)
            try:
                self.add_more_button.disconnect_by_func(lambda btn: self.navigation_view.push_by_tag("transcripts"))
            except:
                pass
            try:
                self.add_more_button.disconnect_by_func(self.on_add_audio)
            except:
                pass
            self.add_more_button.connect("clicked", self.on_add_audio)

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
            listbox > row:selected {
                background-color: transparent;
                color: inherit;
            }
            spinner {
                -gtk-icon-size: 16px;
            }
            .highlight {
                background-color: yellow;
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
            dialog.connect("response", lambda d, r: self._on_cancel_transcription_response(d, r))
            dialog.present(self.window)
        elif current_tag in ("details", "transcripts", "transcript_details", "file_content"):
            self.navigation_view.pop_to_tag("welcome")
            GLib.idle_add(self._reset_btn)

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
            self._remove_all_files()
            self.navigation_view.pop_to_tag("welcome")
            GLib.idle_add(self._reset_btn)

    def _on_back_clicked(self):
        current_tag = self.navigation_view.get_visible_page().get_tag()
        if current_tag == "review":
            self.navigation_view.pop_to_tag("welcome")
            GLib.idle_add(self._reset_btn)
        elif current_tag in ("details", "transcript_details"):
            self.navigation_view.pop()
        elif current_tag == "file_content":
            self.navigation_view.pop_to_tag("review")

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

    def _clear_listbox(self, listbox):
        print("Clearing listbox")  # Debug
        try:
            while True:
                child = listbox.get_first_child()
                if child is None:
                    break
                listbox.remove(child)
            print("Listbox cleared successfully")  # Debug
        except Exception as e:
            print(f"Error clearing listbox: {e}")  # Debug

    def _load_transcripts(self):
        print("_load_transcripts called")  # Debug
        self._update_transcripts_list("")

    def setup_transcripts_listbox(self):
        print("Setting up transcripts listbox")  # Debug
        if hasattr(self, 'transcripts_group') and self.transcripts_group is not None:
            self.transcripts_group.connect("row-activated", self._on_transcript_row_activated)
            print("Connected row-activated signal")  # Debug
        else:
            print("transcripts_group not found or is None")  # Debug

    def _on_transcript_row_activated(self, listbox, row):
        print("Transcript row activated")  # Debug
        file_path = getattr(row, 'file_path', None)
        print(f"File path: {file_path}")  # Debug
        if file_path:
            self._show_transcript(file_path)
        else:
            print("No file path found for row")  # Debug

    def _show_transcript(self, file_path):
        print(f"_show_transcript called with: {file_path}")  # Debug
        if not os.path.exists(file_path):
            print(f"File does not exist: {file_path}")  # Debug
            return

        transcript_page = Adw.NavigationPage()
        transcript_page.set_title(os.path.basename(file_path))
        transcript_page.set_tag("transcript_details")

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)

        status_page = Adw.StatusPage()
        status_page.set_title(os.path.basename(file_path))
        status_page.set_icon_name("text-x-generic-symbolic")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            print(f"Read {len(lines)} lines from {file_path}")  # Debug

            buffer = Gtk.TextBuffer()
            highlight_tag = buffer.create_tag("highlight", background="yellow")
            search_text = self.search_entry.get_text().strip().lower()

            text_with_numbers = ""
            for i, line in enumerate(lines, 1):
                text_with_numbers += f"{i:4d} | {line}"
            buffer.set_text(text_with_numbers)

            if search_text:
                text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).lower()
                start_pos = 0
                while True:
                    start_pos = text.find(search_text, start_pos)
                    if start_pos == -1:
                        break
                    start_iter = buffer.get_iter_at_offset(start_pos)
                    end_iter = buffer.get_iter_at_offset(start_pos + len(search_text))
                    buffer.apply_tag(highlight_tag, start_iter, end_iter)
                    start_pos += len(search_text)

            output_box = self.create_output_widget({'buffer': buffer})
            status_page.set_child(output_box)

            open_btn = Gtk.Button()
            open_btn.set_icon_name("folder-open-symbolic")
            open_btn.set_valign(Gtk.Align.CENTER)
            open_btn.add_css_class("flat")
            open_btn.set_tooltip_text("Open transcript in default editor")
            open_btn.connect("clicked", lambda btn: self._open_transcript_file(file_path))

            content.append(status_page)
            content.append(open_btn)
        except Exception as e:
            print(f"Error loading transcript: {e}")  # Debug
            status_page.set_description(f"Error loading transcript: {e}")
            content.append(status_page)

        content_clamp = Adw.Clamp()
        content_clamp.set_child(content)
        scrolled.set_child(content_clamp)
        transcript_page.set_child(scrolled)

        print("Pushing transcript page")  # Debug
        self.navigation_view.push(transcript_page)
        print("Transcript page pushed")  # Debug

    def _open_transcript_file(self, file_path):
        try:
            subprocess.run(["xdg-open", str(file_path)], check=True)
        except subprocess.CalledProcessError as e:
            GLib.idle_add(self._error, f"Failed to open transcript: {e}")

    def on_search_changed(self, entry):
        search_text = entry.get_text().strip()
        self._update_transcripts_list(search_text)

    def _update_transcripts_list(self, search_text):
        print(f"_update_transcripts_list called with search_text: '{search_text}'")  # Debug
        self._clear_listbox(self.transcripts_group)

        out_dir = self.output_directory or os.path.expanduser("~/Downloads")
        print(f"Looking for transcripts in: {out_dir}")  # Debug

        if not os.path.isdir(out_dir):
            print(f"Output directory does not exist: {out_dir}")  # Debug
            self._error("Output directory is invalid.")
            return

        matches = []
        try:
            total_files = 0
            txt_files = 0
            for root, _, files in sorted(os.walk(out_dir)):
                print(f"Checking directory: {root}")  # Debug
                for file in sorted(files):
                    total_files += 1
                    if file.endswith(".txt"):
                        txt_files += 1
                        file_path = os.path.join(root, file)
                        print(f"Found txt file: {file_path}")  # Debug
                        if search_text:
                            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                                if search_text.lower() in content.lower():
                                    matches.append(file_path)
                                    print(f"Match found: {file_path}")  # Debug
                        else:
                            matches.append(file_path)

            print(f"Total files: {total_files}, txt files: {txt_files}, matches: {len(matches)}")  # Debug

            if not matches and search_text:
                toast = Adw.Toast(title="No matches found")
                toast.set_timeout(3)
                self.toast_overlay.add_toast(toast)

            if not matches and not search_text:
                print("No txt files found")  # Debug
                row = Gtk.ListBoxRow()
                row_content = Adw.ActionRow()
                row_content.set_title("No transcripts found")
                row_content.set_subtitle(f"No .txt files in {out_dir}")
                row.set_child(row_content)
                self.transcripts_group.append(row)
                return

            for file_path in matches:
                print(f"Adding row for: {file_path}")  # Debug
                row = Gtk.ListBoxRow()
                row_content = Adw.ActionRow()
                row_content.set_title(os.path.basename(file_path))
                row_content.set_subtitle(file_path)
                row.file_path = file_path  # Store file path as attribute

                open_btn = Gtk.Button()
                open_btn.set_icon_name("folder-open-symbolic")
                open_btn.set_valign(Gtk.Align.CENTER)
                open_btn.add_css_class("flat")
                open_btn.set_tooltip_text("Open transcript in default editor")
                open_btn.connect("clicked", lambda btn, path=file_path: self._open_transcript_file(path))
                row_content.add_suffix(open_btn)

                row.set_child(row_content)
                self.transcripts_group.append(row)
            print(f"Added {len(matches)} rows")  # Debug

        except Exception as e:
            print(f"Error in _update_transcripts_list: {e}")  # Debug
            import traceback
            traceback.print_exc()
            self._error(f"Failed to load transcripts: {e}")

if __name__ == "__main__":
    app = WhisperApp()
    app.run()







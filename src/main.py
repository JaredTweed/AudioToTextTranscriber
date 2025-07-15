import gi
import re
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

HOME_DIR = str(Path.home())

def _human_path(path: str) -> str:
    """Return path with $HOME abbreviated to ~, if applicable."""
    if not path:
        return path
    path = str(path)
    if path == HOME_DIR:
        return "~"
    if path.startswith(HOME_DIR + os.sep):
        return "~" + path[len(HOME_DIR):]
    return path


class WhisperApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.JaredTweed.AudioToTextTranscriber")
        self._highlight_buffers: set[Gtk.TextBuffer] = set()
        self.title = "Audio-To-Text Transcriber"
        self.settings_file = Path(GLib.get_user_data_dir()) / "AudioToTextTranscriber" / "Settings.yaml"
        self.load_settings()
        Adw.StyleManager.get_default().connect(
            "notify::dark",
            lambda mgr, _p: self._refresh_highlight_tags()
        )
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
        self.transcript_items = []
        self._scan_handle = 0          # source-id of the debounce timer
        self._scan_thread = None       # background Thread object
        self.transcript_paths  = set()      #  <-- NEW
        self.no_transcripts_row = None      #  <-- NEW
        self.files_group = None
        self.transcripts_group = None
        self.search_entry = None
        self.stack = None
        self.view_switcher = None
        self.trans_btn = None
        self.add_more_button = None
        self.model_value_label = None
        self.output_value_label = None
        self.connect('startup', self.do_startup)
        self.connect('activate', self.do_activate)
        self.create_action("settings", self.on_settings)
        self.setup_transcripts_listbox()

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

    def do_startup(self, *args):
        Adw.Application.do_startup(self)
        self.window = Adw.ApplicationWindow(application=self, title=self.title)
        self.window.set_default_size(600, 800)
        self._build_ui()
        self._setup_dnd()
        self._update_model_btn()

    def do_activate(self, *args):
        self.window.present()

    def create_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def create_view_switcher_ui(self):
        self.stack = Adw.ViewStack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)

        # Transcribe View
        transcribe_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        transcribe_box.set_margin_start(12)
        transcribe_box.set_margin_end(12)
        transcribe_box.set_margin_top(12)
        transcribe_box.set_margin_bottom(12)

        # Transcribe Button
        self.trans_btn = Gtk.Button(label="Transcribe")
        self._green(self.trans_btn)
        self.trans_btn.connect("clicked", self.on_transcribe)
        transcribe_box.append(self.trans_btn)

        # Reset Button
        self.reset_btn = Gtk.Button()
        self.reset_btn.set_icon_name("view-refresh-symbolic")
        self.reset_btn.set_visible(False)
        self.reset_btn.connect("clicked", self._on_reset_clicked)
        transcribe_box.append(self.reset_btn)

        # Add Audio Files Button
        self.add_more_button = Gtk.Button(label="Add Audio Files")
        self.add_more_button.connect("clicked", self.on_add_audio)
        transcribe_box.append(self.add_more_button)

        # Files Group
        self.files_group = Adw.PreferencesGroup()
        self.files_group.set_title("Audio Files")
        self.files_group.set_description("Review files to be transcribed")
        transcribe_box.append(self.files_group)

        transcribe_scrolled = Gtk.ScrolledWindow()
        transcribe_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        transcribe_scrolled.set_vexpand(True)
        transcribe_scrolled.set_hexpand(True)
        transcribe_scrolled.set_child(transcribe_box)

        page = self.stack.add_titled(transcribe_scrolled, "transcribe", "Transcriber")
        # Gtk / libadwaita ≤ 1.4
        page.set_icon_name("media-record-symbolic")        # any symbolic name works

        # libadwaita ≥ 1.4 (optionally – keeps older versions happy)
        if hasattr(page, "set_icon"):                      # new API, accepts Gio.Icon
            page.set_icon(Gio.ThemedIcon.new("media-record-symbolic"))

        # View Transcripts View
        transcripts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        transcripts_box.set_margin_start(12)
        transcripts_box.set_margin_end(12)
        transcripts_box.set_margin_top(12)
        transcripts_box.set_margin_bottom(12)

        # Search and Close Buttons
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search in transcripts...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        search_bar.append(self.search_entry)

        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.set_tooltip_text("Close search")
        close_btn.connect("clicked", lambda btn: self.search_entry.set_text(""))
        search_bar.append(close_btn)

        transcripts_box.append(search_bar)

        self.transcripts_group = Adw.PreferencesGroup()
        self.transcripts_group.set_title("Transcripts")
        self.transcripts_group.set_description("View completed transcripts")
        transcripts_box.append(self.transcripts_group)

        transcripts_scrolled = Gtk.ScrolledWindow()
        transcripts_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        transcripts_scrolled.set_vexpand(True)
        transcripts_scrolled.set_hexpand(True)
        transcripts_scrolled.set_child(transcripts_box)

        page = self.stack.add_titled(transcripts_scrolled, "transcripts", "Transcripts")
        
        page.set_icon_name("text-x-generic-symbolic")
        if hasattr(page, "set_icon"):
            page.set_icon(Gio.ThemedIcon.new("text-x-generic-symbolic"))

        # View Switcher
        self.view_switcher = Adw.ViewSwitcher()
        self.view_switcher.set_stack(self.stack)
        self.view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        # Connect to stack's visible-child signal to update transcripts when switched
        self.stack.connect("notify::visible-child-name", self._on_view_switched)

    def _on_view_switched(self, stack, param):
        if stack.get_visible_child_name() == "transcripts":
            self._update_transcripts_list(self.search_entry.get_text().strip())

    def _on_reset_clicked(self, button):
        self._reset_btn()
        self.reset_btn.set_visible(False)
        self.add_more_button.set_visible(True)

    def _on_icon_dnd_drop(self, drop_target, value, x, y, button):
        files = value.get_files()
        new_paths = self._collect_audio_files(files)
        for path in new_paths:
            if path not in [item['path'] for item in self.progress_items]:
                self.audio_store.append(path)
                self.add_file_to_list(os.path.basename(path), path)
        if new_paths:
            toast = Adw.Toast(title=f"Added {len(new_paths)} file(s)")
            toast.set_timeout(3)
            self.toast_overlay.add_toast(toast)
        self.stack.set_visible_child_name("transcribe")
        return True

    def add_file_to_list(self, filename, file_path):
        if not self.files_group:
            raise RuntimeError("Files group not initialized.")

        file_row = Adw.ActionRow()
        file_row.set_title(filename)
        file_row.set_subtitle(_human_path(os.path.dirname(file_path)) or "Local File")

        progress_widget = Gtk.Image()
        file_row.add_suffix(progress_widget)

        remove_btn = Gtk.Button()
        remove_btn.set_icon_name("user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.add_css_class("destructive-action")
        remove_btn.set_tooltip_text("Remove file")
        file_row.add_suffix(remove_btn)
        remove_btn.connect("clicked", self._on_remove_file, file_path)

        output_buffer = Gtk.TextBuffer()
        self._ensure_highlight_tag(output_buffer)
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
        return file_data

    def add_transcript_to_list(self, filename, file_path):
        # ‼️  Ignore duplicates completely
        if file_path in self.transcript_paths:
            return

        # If the “no transcripts” placeholder is showing, remove it
        if self.no_transcripts_row and self.no_transcripts_row.get_parent():
            self.transcripts_group.remove(self.no_transcripts_row)
            self.no_transcripts_row = None

        if not self.transcripts_group:
            raise RuntimeError("Transcripts group not initialized.")

        transcript_row = Adw.ActionRow()
        transcript_row.set_title(filename)
        transcript_row.set_subtitle(_human_path(os.path.dirname(file_path)) or "Local File")

        open_btn = Gtk.Button()
        open_btn.set_icon_name("folder-open-symbolic")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.add_css_class("flat")
        open_btn.set_tooltip_text("Open transcript in default editor")
        open_btn.connect("clicked", lambda btn: self._open_transcript_file(file_path))
        transcript_row.add_suffix(open_btn)

        output_buffer = Gtk.TextBuffer()
        self._ensure_highlight_tag(output_buffer) 
        output_view = Gtk.TextView.new_with_buffer(output_buffer)
        output_view.set_editable(False)
        output_view.set_monospace(True)
        output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                output_buffer.set_text(content)
        except Exception as e:
            output_buffer.set_text(f"Error loading transcript: {e}")

        transcript_data = {
            'row': transcript_row,
            'open_btn': open_btn,
            'filename': filename,
            'path': file_path,
            'buffer': output_buffer,
            'view': output_view,
            'is_viewed': False
        }
        self.transcript_items.append(transcript_data)
        self.transcript_paths.add(file_path)

        transcript_row.set_activatable(True)
        transcript_row.connect('activated', lambda r: self._show_transcript_content(transcript_data))
        self.transcripts_group.add(transcript_row)
        return transcript_data

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
        if not self.progress_items:
            self._show_no_files_message()

    def _show_no_files_message(self):
        pass

    def show_file_details(self, file_data):
        details_window = Adw.Window()        # Adw.Window itself has no orientation
        details_window.set_title("File Details")
        details_window.set_default_size(400, 300)

        details_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        details_window.set_content(details_content)
        details_window.present()

    def _show_file_content(self, file_data):
        content_window = Adw.Window()
        content_window.set_title("File Content")
        content_window.set_default_size(400, 300)

        # Create toolbar view with header bar
        toolbar_view = Adw.ToolbarView()
        
        # Create header bar with close button
        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Adw.WindowTitle(title=file_data['filename']))
        
        toolbar_view.add_top_bar(header_bar)

        # Main content box with padding
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_margin_top(20)
        main_box.set_margin_bottom(20)
        main_box.set_margin_start(20)
        main_box.set_margin_end(20)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        if file_data['buffer'] and file_data['buffer'].get_char_count() > 0:
            # Search entry below titlebar
            search_entry = Gtk.SearchEntry()
            search_entry.set_placeholder_text("Search in content...")
            search_entry.set_hexpand(True)
            
            buffer = Gtk.TextBuffer()
            # Check if highlight tag exists
            self._ensure_highlight_tag(buffer)
            highlight_tag = buffer.get_tag_table().lookup("highlight")

            text = file_data['buffer'].get_text(
                file_data['buffer'].get_start_iter(),
                file_data['buffer'].get_end_iter(),
                False
            )
            lines = text.splitlines()
            search_text = self.search_entry.get_text().strip().lower()
            text_with_numbers = ""
            for i, line in enumerate(lines, 1):
                text_with_numbers += f"{i:4d} | {line}\n"
            buffer.set_text(text_with_numbers)

            if search_text:
                text_lower = text_with_numbers.lower()
                start_pos = 0
                while True:
                    start_pos = text_lower.find(search_text, start_pos)
                    if start_pos == -1:
                        break
                    start_iter = buffer.get_iter_at_offset(start_pos)
                    end_iter = buffer.get_iter_at_offset(start_pos + len(search_text))
                    buffer.apply_tag(highlight_tag, start_iter, end_iter)
                    start_pos += len(search_text)

            text_view = Gtk.TextView.new_with_buffer(buffer)
            text_view.set_editable(False)
            text_view.set_monospace(True)
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

            scrolled_view = Gtk.ScrolledWindow()
            scrolled_view.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_view.set_vexpand(True)
            scrolled_view.set_hexpand(True)
            scrolled_view.set_child(text_view)

            search_entry.connect("search-changed", lambda entry: self._highlight_text(text_view, entry.get_text().strip()))

            content_box.append(search_entry)
            content_box.append(scrolled_view)
        else:
            status_msg = Gtk.Label(label="No transcription content available.")
            content_box.append(status_msg)

        main_box.append(content_box)
        toolbar_view.set_content(main_box)
        content_window.set_content(toolbar_view)
        content_window.present()

    def _show_transcript_content(self, transcript_data):
        content_window = Adw.Window()
        content_window.set_title("Transcript Content")
        content_window.set_default_size(400, 300)

        # Create toolbar view with header bar
        toolbar_view = Adw.ToolbarView()
        
        # Create header bar with close button
        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Adw.WindowTitle(title=transcript_data['filename']))

        
        toolbar_view.add_top_bar(header_bar)

        # Main content box with padding
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_margin_top(20)
        main_box.set_margin_bottom(20)
        main_box.set_margin_start(20)
        main_box.set_margin_end(20)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        if transcript_data['buffer'] and transcript_data['buffer'].get_char_count() > 0:
            # Search entry below titlebar
            search_entry = Gtk.SearchEntry()
            search_entry.set_placeholder_text("Search in content...")
            search_entry.set_hexpand(True)
            
            buffer = Gtk.TextBuffer()
            # Check if highlight tag exists
            self._ensure_highlight_tag(buffer)
            highlight_tag = buffer.get_tag_table().lookup("highlight")

            text = transcript_data['buffer'].get_text(
                transcript_data['buffer'].get_start_iter(),
                transcript_data['buffer'].get_end_iter(),
                False
            )
            lines = text.splitlines()
            search_text = self.search_entry.get_text().strip().lower()
            text_with_numbers = ""
            for i, line in enumerate(lines, 1):
                text_with_numbers += f"{i:4d} | {line}\n"
            buffer.set_text(text_with_numbers)

            if search_text:
                text_lower = text_with_numbers.lower()
                start_pos = 0
                while True:
                    start_pos = text_lower.find(search_text, start_pos)
                    if start_pos == -1:
                        break
                    start_iter = buffer.get_iter_at_offset(start_pos)
                    end_iter = buffer.get_iter_at_offset(start_pos + len(search_text))
                    buffer.apply_tag(highlight_tag, start_iter, end_iter)
                    start_pos += len(search_text)

            text_view = Gtk.TextView.new_with_buffer(buffer)
            text_view.set_editable(False)
            text_view.set_monospace(True)
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

            scrolled_view = Gtk.ScrolledWindow()
            scrolled_view.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_view.set_vexpand(True)
            scrolled_view.set_hexpand(True)
            scrolled_view.set_child(text_view)

            search_entry.connect("search-changed", lambda entry: self._highlight_text(text_view, entry.get_text().strip()))

            content_box.append(search_entry)
            content_box.append(scrolled_view)
        else:
            status_msg = Gtk.Label(label="No transcription content available.")
            content_box.append(status_msg)

        main_box.append(content_box)
        toolbar_view.set_content(main_box)
        content_window.set_content(toolbar_view)
        content_window.present()

    def _ensure_highlight_tag(self, buffer: Gtk.TextBuffer):
        self._highlight_buffers.add(buffer)

        style_mgr = Adw.StyleManager.get_default()
        dark      = style_mgr.get_dark()

        # pleasant pastel yellow for light, amber-500 for dark
        light_rgba = Gdk.RGBA(); light_rgba.parse("#ffe600")
        dark_rgba  = Gdk.RGBA(); dark_rgba.parse("#b87700")       #  36 % L*

        tag_table = buffer.get_tag_table()
        tag = tag_table.lookup("highlight")
        if tag is None:
            tag = buffer.create_tag("highlight")

        tag.set_property("background-rgba", dark_rgba if dark else light_rgba)

    def _refresh_highlight_tags(self):
        """
        Called automatically when Adwaita switches between light/dark.
        We simply re-apply the right colour on every buffer we know about.
        """
        for buf in list(self._highlight_buffers):
            # the buffer might have been destroyed – skip if so
            if buf.__grefcount__ == 0:
                self._highlight_buffers.discard(buf)
                continue
            self._ensure_highlight_tag(buf)

    def _highlight_text(self, text_view, search_text: str):
        buf = text_view.get_buffer()
        buf.remove_tag_by_name("highlight", buf.get_start_iter(), buf.get_end_iter())
        if not search_text:
            return

        pattern = re.compile(re.escape(search_text), re.IGNORECASE | re.UNICODE)
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

        for m in pattern.finditer(txt):
            start_it = buf.get_iter_at_offset(m.start())
            end_it   = buf.get_iter_at_offset(m.end())
            buf.apply_tag_by_name("highlight", start_it, end_it)

    def create_output_widget(self, data):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)

        text_view = Gtk.TextView.new_with_buffer(data['buffer'])
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        scrolled.set_child(text_view)
        return scrolled

    def update_file_status(self, file_data, status, message=""):
        row, old_icon, remove_btn = file_data['row'], file_data['icon'], file_data['remove_btn']

        if old_icon and old_icon.get_parent():
            row.remove(old_icon)

        if status == 'processing':
            new_icon = Gtk.Spinner()
            new_icon.set_spinning(True)
        else:
            icon_name = {
                'waiting':   None,
                'completed': None,
                'cancelled': 'process-stop-symbolic',
                'error':     'dialog-error-symbolic',
                'skipped':   'dialog-information-symbolic',
            }.get(status, None)          # ← default is also None
            new_icon = Gtk.Image.new_from_icon_name(icon_name) if icon_name else Gtk.Image()

        if remove_btn.get_parent():
            row.remove(remove_btn)
        row.add_suffix(new_icon)
        row.add_suffix(remove_btn)

        file_data['icon'] = new_icon
        file_data['status'] = status
        # NB: during processing we overwrite the subtitle live from _worker,
        # so here we only set an initial value or the final result.
        row.set_subtitle(message or status.title())

    def add_log_text(self, file_data, text):
        if file_data['buffer']:
            end_iter = file_data['buffer'].get_end_iter()
            file_data['buffer'].insert(end_iter, text + "\n")

    def on_about(self, action, param):
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name=self.title,
            application_icon="io.github.JaredTweed.AudioToTextTranscriber",
            version="1.0",
            developers=["Jared Tweed", "Mohammed Asif Ali Rizvan"],
            license_type=Gtk.License.GPL_3_0,
            comments="A GUI for whisper.cpp to transcribe audio files.",
            website="https://github.com/JaredTweed/AudioToTextTranscriber",
        )
        about.present()

    def on_toggle_timestamps(self, action, param):
        self.ts_enabled = not self.ts_enabled
        action.set_state(GLib.Variant.new_boolean(self.ts_enabled))

    def _on_model_combo_changed(self, dropdown, _):
        self.save_settings()
        self._update_model_btn()

    def _model_target_path(self, core):
        return os.path.join(self.models_dir, f"ggml-{core}.bin")

    def _display_name(self, core: str) -> str:
        return next((
            label for label, c in self.display_to_core.items() if c == core
        ), core)

    def _get_model_name(self):
        selected_index = self.model_combo.get_selected() if self.model_combo else Gtk.INVALID_LIST_POSITION
        if selected_index == Gtk.INVALID_LIST_POSITION or self.model_strings.get_n_items() == 0:
            return "None"
        active = self.model_strings.get_string(selected_index)
        return self.display_to_core.get(active, "None")

    def _update_model_btn(self):
        selected_index = self.model_combo.get_selected() if self.model_combo else Gtk.INVALID_LIST_POSITION
        if selected_index == Gtk.INVALID_LIST_POSITION or self.model_strings.get_n_items() == 0:
            if self.model_btn:
                self.model_btn.set_label("No Model Selected")
            if hasattr(self, 'trans_btn'):
                self.trans_btn.set_sensitive(False)
            if self.model_value_label:
                self.model_value_label.set_label("None")
            return False

        active = self.model_strings.get_string(selected_index)
        if not active:
            if self.model_btn:
                self.model_btn.set_label("No Model Selected")
            if hasattr(self, 'trans_btn'):
                self.trans_btn.set_sensitive(False)
            if self.model_value_label:
                self.model_value_label.set_label("None")
            return False

        if self.dl_info:
            done = os.path.getsize(self.dl_info["target"]) // MB if os.path.isfile(self.dl_info["target"]) else 0
            tot = self.dl_info["total_mb"] or "?"
            if self.model_btn:
                self.model_btn.set_label(f"Cancel Download {done} / {tot} MB")
            if hasattr(self, 'trans_btn'):
                self.trans_btn.set_sensitive(False)
            return True

        try:
            core = self.display_to_core[active]
        except KeyError:
            if self.model_btn:
                self.model_btn.set_label("No Model Selected, Goto Settings!")
            if hasattr(self, 'trans_btn'):
                self.trans_btn.set_sensitive(False)
            if self.model_value_label:
                self.model_value_label.set_label("None")
            return False

        exists = os.path.isfile(self._model_target_path(core))
        if self.model_btn:
            self.model_btn.set_label("Delete Model" if exists else "Install Model")
        if hasattr(self, 'trans_btn'):
            self.trans_btn.set_sensitive(exists and not self.dl_info)
        if self.model_value_label:
            self.model_value_label.set_label(self._display_name(core))
        if self.output_value_label:
            self.output_value_label.set_label(
                _human_path(self.output_directory) if self.output_directory
                else "Not set"
            )
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
                if fn not in [item['path'] for item in self.progress_items]:
                    self.audio_store.append(fn)
                    self.add_file_to_list(os.path.basename(fn), fn)
            if new_paths:
                toast = Adw.Toast(title=f"Added {len(new_paths)} file(s)")
                toast.set_timeout(3)
                self.toast_overlay.add_toast(toast)
            self.stack.set_visible_child_name("transcribe")
        except GLib.Error:
            pass

    def _on_add_folders_response(self, dialog, result):
        try:
            folders = dialog.select_multiple_folders_finish(result)
            new_paths = self._collect_audio_files(folders)
            for fn in new_paths:
                if fn not in [item['path'] for item in self.progress_items]:
                    self.audio_store.append(fn)
                    self.add_file_to_list(os.path.basename(fn), fn)
            if new_paths:
                toast = Adw.Toast(title=f"Added {len(new_paths)} file(s)")
                toast.set_timeout(3)
                self.toast_overlay.add_toast(toast)
            self.stack.set_visible_child_name("transcribe")
        except GLib.Error:
            pass

    def _collect_audio_files(self, files):
        audio_ext = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus")
        found = []
        seen = set(self.audio_store.get_string(i) for i in range(self.audio_store.get_n_items()))
        seen.update(item['path'] for item in self.progress_items)
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

    def _remove_all_files(self):
        for file_data in self.progress_items:
            self.files_group.remove(file_data['row'])
        self.progress_items.clear()
        self.audio_store.splice(0, self.audio_store.get_n_items(), [])
        self._show_no_files_message()

    def _setup_dnd(self):
        target = Gtk.DropTarget.new(type=Gdk.FileList, actions=Gdk.DragAction.COPY)
        target.connect("drop", self._on_dnd_drop)
        self.files_group.add_controller(target)

    def _on_dnd_drop(self, drop_target, value, x, y):
        files = value.get_files()
        new_paths = self._collect_audio_files(files)
        for path in new_paths:
            if path not in [item['path'] for item in self.progress_items]:
                self.audio_store.append(path)
                self.add_file_to_list(os.path.basename(path), path)
        if new_paths:
            toast = Adw.Toast(title=f"Added {len(new_paths)} file(s)")
            toast.set_timeout(3)
            self.toast_overlay.add_toast(toast)
        self.stack.set_visible_child_name("transcribe")
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
            return


        self._reset_rows_if_needed()

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

        conflicting_files = []
        non_conflicting_files = []
        for file_path in files:
            filename = os.path.basename(file_path)
            dest = os.path.join(out_dir, os.path.splitext(filename)[0] + "_transcribed.txt")
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

    # -----------------------------------------------------------------
    # HELPER: put rows back to a pristine “waiting” state
    # -----------------------------------------------------------------
    def _reset_rows_if_needed(self):
        for file_data in self.progress_items:
            default_sub = _human_path(os.path.dirname(file_data['path'])) or "Local File"

            # We need to reset if:
            #   • the previous icon shows a cancelled / error status
            #   • OR the subtitle isn’t the default one any more
            if file_data['status'] in ('error', 'cancelled') \
               or file_data['row'].get_subtitle() != default_sub:

                # Clear any old log text
                if file_data['buffer']:
                    file_data['buffer'].set_text("")

                # Bring row back to “waiting” with its default subtitle
                self.update_file_status(file_data, 'waiting', default_sub)

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

    def _start_transcription(self, files, model_path, out_dir, core):
        self.cancel_flag = False
        self.trans_btn.set_label("Cancel")
        self._red(self.trans_btn)
        GLib.idle_add(self.status_lbl.set_label, "Transcription Started")
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

            cmd = [self.bin_path, "-m", model_path, "-f", file_path, "-pp"]
            if not self.ts_enabled:
                cmd.append("-nt")

            # keep the streams separate:
            #   · stdout  → transcript (plus a few noisy lines we’ll drop)
            #   · stderr  → progress updates + errors
            self.current_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                errors='replace'
            )

            # ── WATCH STDERR FOR PERCENT, LIVE ───────────────────────────
            def _watch_stderr(proc, row, idx, total):
                buf      = ""           # rolling buffer holding the current line
                last_pct = None         # last % we showed, to avoid spam

                while True:
                    ch = proc.stderr.read(1)          # read *one* char at a time
                    if not ch:                        # EOF – done
                        # process whatever is left in buf once more
                        _maybe_update(buf, row, idx, total, last_pct)
                        break

                    if ch in ("\r", "\n"):            # line boundary
                        last_pct = _maybe_update(buf, row, idx, total, last_pct)
                        buf = ""                      # start fresh
                    else:
                        buf += ch

            def _maybe_update(line, row, idx, total, last_pct):
                m = re.search(r"progress\s*=\s*([\d.]+)%", line)
                if m:
                    pct = m.group(1)
                    if pct != last_pct:               # only if it really changed
                        GLib.idle_add(
                            row.set_subtitle,
                            f"Transcribing ({idx}/{total}) — {pct}%"
                        )
                    return pct                        # new “last” value
                return last_pct

            threading.Thread(
                target=_watch_stderr,
                args=(self.current_proc, file_data['row'], idx, total),
                daemon=True
            ).start()

            # ── READ stdout and keep only the real transcript ───────────
            ts_line   = re.compile(r"^\[\d\d:\d\d:\d\d")   # with timestamps

            for line in self.current_proc.stdout:
                if self.cancel_flag:
                    try:
                        self.current_proc.terminate()
                    except:
                        pass
                    GLib.idle_add(self.update_file_status, file_data, 'error', "Cancelled")
                    GLib.idle_add(self.add_log_text, file_data, "Transcription cancelled")
                    break

                # 1. drop completely empty lines
                if not line.strip():
                    continue

                # 2. keep only “real” transcript lines
                keep = False
                if self.ts_enabled:
                    # we expect the [hh:mm:ss.xxx --> yy:...] format
                    keep = bool(ts_line.match(line))
                else:
                    # without timestamps: reject lines that *look* like logs
                    keep = not line.lstrip().startswith(("whisper_", "system_info",
                                                         "main:", "whisper_print_timings"))

                if keep:
                    GLib.idle_add(self.add_log_text, file_data, line.rstrip())

            self.current_proc.stdout.close()
            self.current_proc.wait()

            if self.cancel_flag:
                GLib.idle_add(self.update_file_status, file_data, 'error', "Cancelled")
            else:
                if self.current_proc.returncode != 0:
                    # read remaining stderr so we can show the error
                    err_msg = self.current_proc.stderr.read().strip()
                    GLib.idle_add(
                        self.update_file_status,
                        file_data, 'error',
                        f"Failed (exit {self.current_proc.returncode})"
                    )
                    GLib.idle_add(
                        self.add_log_text,
                        file_data,
                        f"ERROR: {err_msg or 'process exited with code ' + str(self.current_proc.returncode)}"
                    )
                else:
                    dest = os.path.join(out_dir, os.path.splitext(filename)[0] + "_transcribed.txt")
                    def _save():
                        if file_data and 'buffer' in file_data and file_data['buffer']:
                            txt = file_data['buffer'].get_text(
                                file_data['buffer'].get_start_iter(),
                                file_data['buffer'].get_end_iter(),
                                False
                            )
                            with open(dest, "w", encoding="utf-8") as f:
                                f.write(txt)
                            if dest not in [item['path'] for item in self.transcript_items]:
                                GLib.idle_add(self.add_transcript_to_list, os.path.basename(dest), dest)
                        return False
                    GLib.idle_add(_save)
                    GLib.idle_add(self.update_file_status, file_data, 'completed', "Completed successfully")

        if self.cancel_flag:
            self._gui_status("Cancelled")
            GLib.idle_add(self._reset_btn)
            GLib.idle_add(self.reset_btn.set_visible, False)   # keep it hidden
            GLib.idle_add(self.add_more_button.set_visible, True)
        else:
            GLib.idle_add(self.reset_btn.set_visible, True)    # show only on success
            GLib.idle_add(self.add_more_button.set_visible, False)

            self._gui_status("Done")
            GLib.idle_add(self.trans_btn.set_label, "Transcription Complete")
            GLib.idle_add(self.trans_btn.set_sensitive, False)
            GLib.idle_add(self.add_more_button.set_label, "View Transcripts")
            try:
                self.add_more_button.disconnect_by_func(self.on_add_audio)
            except TypeError:
                pass
            GLib.idle_add(self.add_more_button.connect, "clicked", lambda btn: self.stack.set_visible_child_name("transcripts"))

    def _green(self, b):
        b.add_css_class("suggested-action")
        b.remove_css_class("destructive-action")

    def _red(self, b):
        b.add_css_class("destructive-action")
        b.remove_css_class("suggested-action")

    def _gui_status(self, msg):
        GLib.idle_add(self.status_lbl.set_label, msg)

    def _reset_btn(self):
        self.trans_btn.set_label("Transcribe")
        self._green(self.trans_btn)
        self.trans_btn.set_sensitive(self._update_model_btn())
        if self.add_more_button:
            self.add_more_button.set_label("Add Audio Files")
            self.add_more_button.set_visible(True)
            try:
                self.add_more_button.disconnect_by_func(lambda btn: self.stack.set_visible_child_name("transcripts"))
            except TypeError:
                pass
            try:
                self.add_more_button.disconnect_by_func(self.on_add_audio)
            except TypeError:
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
        toast = Adw.Toast(title=msg, button_label="Close")
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
        self.output_settings_row.set_subtitle(_human_path(self.output_directory))
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
        """.encode('utf-8'))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.header_bar = Adw.HeaderBar()
        self.header_bar.add_css_class("flat")
        main_box.append(self.header_bar)

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
        menu.append("Clear All Audio", "app.remove-all-audio")
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

        self.create_view_switcher_ui()
        main_box.append(self.view_switcher)
        main_box.append(self.stack)

        # ── footer ─────────────────────────────────────────────
        # Line 1 : Model          (Status)
        model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        model_box.set_halign(Gtk.Align.START)

        model_box.append(Gtk.Label(label="Model: "))
        self.model_value_label = Gtk.Label(
            label=self._display_name(self._get_model_name())
        )
        model_box.append(self.model_value_label)

        model_box.append(Gtk.Label(label=" ("))
        self.status_lbl = Gtk.Label(label="Idle")          # <- status now lives here
        model_box.append(self.status_lbl)
        model_box.append(Gtk.Label(label=")"))

        main_box.append(model_box)

        # Line 2 : Output Directory          [Settings]
        output_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        output_box.set_halign(Gtk.Align.START)

        output_box.append(Gtk.Label(label="Output Directory: "))
        self.output_value_label = Gtk.Label(
            label=_human_path(self.output_directory) or "Not set"
        )
        output_box.append(self.output_value_label)

        # settings_button = Gtk.Button(label="Settings")
        # settings_button.connect("clicked",
        #                         lambda btn: self.on_settings(None, None))
        # output_box.append(settings_button)

        main_box.append(output_box)

        self._refresh_model_menu()
        self._update_model_btn()

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
        try:
            while child := listbox.get_first_child():
                listbox.remove(child)
        except Exception as e:
            print(f"Error clearing listbox: {e}")

    def setup_transcripts_listbox(self):
        if hasattr(self, 'transcripts_group') and self.transcripts_group is not None:
            pass

    def _show_transcript(self, file_path):
        if not os.path.exists(file_path):
            return

        transcript_window = Adw.Window()
        transcript_window.set_title(os.path.basename(file_path))
        transcript_window.set_default_size(400, 300)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            buffer = Gtk.TextBuffer()
            self._ensure_highlight_tag(buffer)
            highlight_tag = buffer.get_tag_table().lookup("highlight")

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


            open_btn = Gtk.Button()
            open_btn.set_icon_name("folder-open-symbolic")
            open_btn.set_valign(Gtk.Align.CENTER)
            open_btn.add_css_class("flat")
            open_btn.set_tooltip_text("Open transcript in default editor")
            open_btn.connect("clicked", lambda btn: self._open_transcript_file(file_path))

            content.append(output_box)
            content.append(open_btn)
        except Exception as e:
            status_msg = Gtk.Label(label=f"Error loading transcript: {e}")
            content.append(status_msg)

        transcript_window.set_content(content)
        transcript_window.present()

    def _open_transcript_file(self, file_path):
        try:
            Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(file_path).get_uri(), None
            )
        except subprocess.CalledProcessError as e:
            GLib.idle_add(self._error, f"Failed to open transcript: {e}")

    def on_search_changed(self, entry: Gtk.SearchEntry):
        text = entry.get_text().strip()
        if self._scan_handle:
            GLib.source_remove(self._scan_handle)
            self._scan_handle = 0          # <-- important
        self._scan_handle = GLib.timeout_add(
            300,
            lambda: (self._spawn_scan_thread(text), False)
        )

    def _spawn_scan_thread(self, search_text: str):
        # if a previous scan is still running just let it finish
        if self._scan_thread and self._scan_thread.is_alive():
            return
        self._scan_thread = threading.Thread(
            target=self._update_transcripts_list,
            args=(search_text,),
            daemon=True,
        )
        self._scan_thread.start()

    

    # ─── replace the whole method ───────────────────────────────────
    def _update_transcripts_list(self, search_text: str):
        matches = []
        out_dir = self.output_directory or os.path.expanduser("~/Downloads")

        try:
            for root, _, files in os.walk(out_dir):
                for fname in files:
                    if not fname.endswith("_transcribed.txt"):
                        continue
                    full = os.path.join(root, fname)
                    if search_text and search_text.lower() not in fname.lower():
                        with open(full, "r", encoding="utf-8", errors="replace") as f:
                            if search_text.lower() not in f.read().lower():
                                continue
                    matches.append(full)
        except Exception as e:
            GLib.idle_add(self._error, f"Failed to scan transcripts: {e}")
            return

        # push the UI changes back onto the main thread
        GLib.idle_add(self._rebuild_transcript_rows, matches)


    def _rebuild_transcript_rows(self, matches: list[str]):
        # 1. Remove rows we previously inserted
        for t in self.transcript_items:
            if t['row'].get_parent():
                self.transcripts_group.remove(t['row'])
        self.transcript_items.clear()
        self.transcript_paths.clear()

        if self.no_transcripts_row and self.no_transcripts_row.get_parent():
            self.transcripts_group.remove(self.no_transcripts_row)
        self.no_transcripts_row = None

        # 2. Show placeholder or rebuild rows
        if not matches:
            self.no_transcripts_row = Adw.ActionRow()
            self.no_transcripts_row.set_title("No transcripts found")
            out_dir = _human_path(self.output_directory or os.path.expanduser("~/Downloads"))
            self.no_transcripts_row.set_subtitle(f"No \"_transcribed.txt\" files in {out_dir}")
            self.transcripts_group.add(self.no_transcripts_row)
            return

        for path in sorted(matches):
            self.add_transcript_to_list(os.path.basename(path), path)



    def _browse_out_settings(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Output Directory")
        dialog.set_accept_label("Select")
        dialog.select_folder(self.window, None, self._on_browse_out_response)

    def _on_browse_out_response(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self.output_directory = folder.get_path()                 # raw path
                self.output_settings_row.set_subtitle(
                    _human_path(self.output_directory)                    # display
                )
                self.save_settings()
                if self.output_value_label:
                    self.output_value_label.set_label(
                        _human_path(self.output_directory)
                    )
        except GLib.Error:
            pass

if __name__ == "__main__":
    app = WhisperApp()
    app.run()

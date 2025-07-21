# main.py
import gi
import os
# ── Work‑around: if the IBus daemon is absent or hung, every key‑press
#    blocks for ±1 s while GTK tries to talk to it over D‑Bus. By forcing
#    the ultra‑light ‘simple’ IM module we bypass IBus entirely.
os.environ.setdefault("GTK_IM_MODULE", "gtk-im-context-simple")

import subprocess
import threading
import yaml
import shutil
import weakref  
from pathlib import Path
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Gio, Gdk, Adw, GObject



# Add current directory to sys.path for local development
import sys
if os.path.dirname(__file__) not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))

try:
    from . import ui
    from . import model
    from . import transcribe
    from . import view_transcripts
    from . import settings
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

class WhisperApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.JaredTweed.AudioToTextTranscriber")
        # WeakSet lets Python release the entry as soon as the last TextView is gone
        self._highlight_buffers: weakref.WeakSet = weakref.WeakSet()
        self.title = "Audio-To-Text Transcriber"
        self.settings_file = Path(GLib.get_user_data_dir()) / "AudioToTextTranscriber" / "Settings.yaml"
        
        Adw.StyleManager.get_default().connect(
            "notify::dark",
            lambda mgr, _p: self._refresh_highlight_tags()
        )
        self.ts_enabled = True
        sd = os.path.abspath(os.path.dirname(__file__))
        
        self.repo_dir = os.path.join(sd, "whisper.cpp") if os.path.isdir(os.path.join(sd, "whisper.cpp")) else sd
        # print(f"Source directory: {sd}\n\nWhisper repo directory: {self.repo_dir}")
        # print(f"ls of source directory: {os.listdir(sd)}")
        # print(f"ls of source directory parent: {os.listdir(os.path.dirname(sd))}")
        # print(f"ls of source directory parent parent: {os.listdir(os.path.dirname(os.path.dirname(sd)))}")
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
        self._scan_cancel = threading.Event() 
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

        # Bind methods from imported modules
        module_methods = {
            ui: [
                'create_view_switcher_ui',
                '_on_view_switched',
                '_on_reset_clicked',
                '_on_dnd_drop',
                'add_file_to_list',
                '_on_remove_file',
                '_on_remove_file_response',
                '_remove_single_file',
                '_show_no_files_message',
                'show_file_details',
                '_show_text_buffer_window',
                '_show_file_content',
                '_ensure_highlight_tag',
                '_refresh_highlight_tags',
                '_highlight_text',
                'create_output_widget',
                'update_file_status',
                'add_log_text',
                'on_about',
                'on_toggle_timestamps',
                '_green',
                '_red',
                '_gui_status',
                '_reset_btn',
                '_yes_no',
                '_error',
                '_on_theme_changed',
                '_build_ui',
                '_browse_out_settings',
                '_on_browse_out_response',
                '_setup_dnd',
                '_on_window_dnd_drop',
            ],
            model: [
                '_on_model_combo_changed',
                '_model_target_path',
                '_display_name',
                '_get_model_name',
                '_update_model_btn',
                'on_model_btn',
                '_on_delete_model',
                '_start_download',
                '_poll_download_progress',
                '_download_model_thread',
                '_on_download_done',
                '_refresh_model_menu',
            ],
            transcribe: [
                'on_add_audio',
                '_on_add_choice_response',
                '_select_audio_files',
                '_select_audio_folders',
                '_on_add_files_response',
                '_on_add_folders_response',
                '_collect_audio_files',
                'on_remove_audio',
                '_on_remove_all_response',
                '_remove_all_files',
                'on_transcribe',
                '_reset_rows_if_needed',
                '_on_conflict_response',
                '_start_transcription',
                '_worker',
                '_update_eta',
                '_audio_seconds',
            ],
            view_transcripts: [
                'add_transcript_to_list',
                '_show_transcript_content',
                '_clear_listbox',
                'setup_transcripts_listbox',
                '_show_transcript',
                '_open_transcript_file',
                'on_search_changed',
                '_spawn_scan_thread',
                '_update_transcripts_list',
                '_rebuild_transcript_rows',
            ],
            settings: [
                'load_settings',
                'save_settings',
                '_on_timestamps_toggled',
                'on_settings',
            ]
        }

        for module, methods in module_methods.items():
            for method_name in methods:
                if hasattr(module, method_name):
                    setattr(self, method_name, getattr(module, method_name).__get__(self, WhisperApp))
        self.load_settings()
        self.create_action("settings", self.on_settings)
        self.setup_transcripts_listbox()

    def do_startup(self, *args):
        Adw.Application.do_startup(self)
        self.window = Adw.ApplicationWindow(application=self, title=self.title)
        self.window.set_default_size(600, 800)
        self.window.connect("close-request", lambda *a: (self.quit(), False)[1])
        self._build_ui()
        self._setup_dnd()
        self._update_model_btn()

    def do_activate(self, *args):
        self.window.present()

    def create_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)


# Add this main function
def main():
    app = WhisperApp()
    app.run()

if __name__ == "__main__":
    main() # Call the main function

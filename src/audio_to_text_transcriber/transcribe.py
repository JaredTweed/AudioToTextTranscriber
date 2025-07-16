# transcribe.py
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

from .helpers import human_path as _hp

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

def _reset_rows_if_needed(self):
    for file_data in self.progress_items:
        default_sub = _hp(os.path.dirname(file_data['path'])) or "Local File"

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
    GLib.idle_add(self.add_more_button.set_visible, False)
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
        GLib.idle_add(self.reset_btn.set_visible, True)    # show reset button
        # GLib.idle_add(self.add_more_button.set_visible, True)

        self._gui_status("Done")
        GLib.idle_add(self.trans_btn.set_label, "Transcription Complete")
        GLib.idle_add(self.trans_btn.set_sensitive, False)

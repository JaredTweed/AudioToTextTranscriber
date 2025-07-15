# model.py
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

from helpers import human_path as _hp

MB = 1024 * 1024

MODEL_SIZE_MB = {
    "tiny":   75,
    "base":   142,
    "small":  466,
    "medium": 1536,
    "large":  2960
}

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
            _hp(self.output_directory) if self.output_directory
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



# view_transcripts.py
import gi
import os, mmap
import re
import subprocess
import threading
import yaml
import shutil
from pathlib import Path
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version("GtkSource", "5")
from gi.repository import Gtk, GLib, Gio, Gdk, Adw, GObject, GtkSource

from .helpers import human_path as _hp

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
    transcript_row.set_subtitle(_hp(os.path.dirname(file_path)) or "Local File")

    open_btn = Gtk.Button()
    open_btn.set_icon_name("folder-open-symbolic")
    open_btn.set_valign(Gtk.Align.CENTER)
    open_btn.add_css_class("flat")
    open_btn.set_tooltip_text("Open transcript in default editor")
    open_btn.connect("clicked", lambda btn: self._open_transcript_file(file_path))
    transcript_row.add_suffix(open_btn)

    transcript_data = {
        'row': transcript_row,
        'open_btn': open_btn,
        'filename': filename,
        'path': file_path,
        'view':   None,
        'is_viewed': False
    }
    self.transcript_items.append(transcript_data)
    self.transcript_paths.add(file_path)

    transcript_row.set_activatable(True)
    transcript_row.connect('activated', lambda r: self._show_transcript_content(transcript_data))
    self.transcripts_group.add(transcript_row)
    return transcript_data

def _show_transcript_content(self, transcript_data):
    # Always build a *new* buffer so nothing lingers in memory
    buf = GtkSource.Buffer()
    self._ensure_highlight_tag(buf)
    try:
        with open(transcript_data['path'], 'r', encoding='utf-8') as fh:
            buf.set_text(fh.read())
    except Exception as e:
        buf.set_text(f"Error loading transcript: {e}")

    # Show it (garbage‑collects automatically when the overlay closes)
    self._show_text_buffer_window(transcript_data['filename'], buf)


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
        self._scan_handle = 0          

    def _run():
        self._scan_handle = 0          # mark as consumed *first*
        self._spawn_scan_thread(text)  # then kick off the scan
        return False                   # one‑shot

    self._scan_handle = GLib.timeout_add(300, _run)

def _spawn_scan_thread(self, search_text):
    if self._scan_thread and self._scan_thread.is_alive():
        self._scan_cancel.set()         # tell old one to stop
    self._scan_cancel = threading.Event()
    self._scan_thread = threading.Thread(
        target=self._update_transcripts_list,
        args=(search_text, self._scan_cancel),
        daemon=True,
    )
    self._scan_thread.start()

def _update_transcripts_list(
        self,
        search_text: str,
        cancel_evt: threading.Event      # ← new
    ):
    """
    Build *matches* quickly and memory‑efficiently.

    • No recursion – all “*_transcribed.txt” files live directly in the
      output directory.
    • If search_text is empty → keep every transcript.
    • Otherwise:
        1.  Keep the file immediately if its **name** contains the term.
        2.  Fallback: stream‑scan the file in 8‑KB chunks (no full read).
    """
    out_dir = self.output_directory or os.path.expanduser("~/Downloads")
    matches: list[str] = []
    hay = search_text.lower() if search_text else ""
    hay_bytes  = hay.encode()

    try:
        for entry in os.scandir(out_dir):
            if cancel_evt.is_set(): return   
            if not entry.name.endswith("_transcribed.txt"):
                continue

            # ① empty search → accept all
            if not hay:
                matches.append(entry.path)
                continue

            # ② filename hit → accept
            if hay in entry.name.lower():
                matches.append(entry.path)
                continue

            # ③ slow path: stream‑scan file contents
            try:
                with open(entry.path, "rb", 0) as fh, \
                     mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    # Compile once per scan and re‑use for every file
                    if '_ci_pat' not in locals():
                        import re
                        _ci_pat = re.compile(re.escape(hay_bytes), re.IGNORECASE)

                    if _ci_pat.search(mm):           # ← case‑insensitive
                        matches.append(entry.path)
            except OSError:
                # unreadable file → silently skip
                pass

    except Exception as e:
        GLib.idle_add(self._error, f"Failed to scan transcripts: {e}")
        return

    # Push UI update onto the main loop
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
        out_dir = self.output_directory or os.path.expanduser("~/Downloads")
        self.no_transcripts_row.set_subtitle(f"No \"_transcribed.txt\" files in {_hp(out_dir)}")
        self.transcripts_group.add(self.no_transcripts_row)
        return

    for path in sorted(matches):
        self.add_transcript_to_list(os.path.basename(path), path)

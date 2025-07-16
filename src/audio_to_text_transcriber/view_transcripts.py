# view_transcripts.py
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

def _show_transcript_content(self, transcript_data):
    # ── 1. Always (re)load the file -----------------------------------------
    try:
        with open(transcript_data["path"], "r", encoding="utf‑8") as f:
            latest_text = f.read()
    except Exception as e:
        latest_text = f"Error loading transcript: {e}"

    if transcript_data.get("buffer") is None:
        transcript_data["buffer"] = Gtk.TextBuffer()

    transcript_data["buffer"].set_text(latest_text)

    # ── 2. Continue building the viewer window ------------------------------
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
        out_dir = self.output_directory or os.path.expanduser("~/Downloads")
        self.no_transcripts_row.set_subtitle(f"No \"_transcribed.txt\" files in {_hp(out_dir)}")
        self.transcripts_group.add(self.no_transcripts_row)
        return

    for path in sorted(matches):
        self.add_transcript_to_list(os.path.basename(path), path)

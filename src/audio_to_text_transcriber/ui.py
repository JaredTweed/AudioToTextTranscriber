
# ui.py
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

_overlay_css_prov = Gtk.CssProvider()
_overlay_css_prov.load_from_data(b"""
#overlay_backdrop { background-color: rgba(0,0,0,0.40); }
#overlay_viewer   { background-image:none; background-color:@window_bg_color; }
""")
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(), _overlay_css_prov,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
)

_viewer_css_prov = Gtk.CssProvider()
_viewer_css_prov.load_from_data(b"""
#overlay_viewer { background-image:none; background-color:@window_bg_color; }
""")
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    _viewer_css_prov,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
)

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

    # Progress Status Text
    self.progress_lbl = Gtk.Label(label="")
    self.progress_lbl.set_use_markup(True)   # allow <b>…</b>
    self.progress_lbl.set_visible(False)
    self.progress_lbl.set_margin_top(8)
    self.progress_lbl.set_margin_bottom(8)
    transcribe_box.append(self.progress_lbl)

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

    # Transcribe Button
    self.trans_btn = Gtk.Button(label="Transcribe")
    self._green(self.trans_btn)
    self.trans_btn.connect("clicked", self.on_transcribe)
    transcribe_box.append(self.trans_btn)

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
    page.set_icon_name("input-keyboard-symbolic")        # any symbolic name works

    # libadwaita ≥ 1.4 (optionally – keeps older versions happy)
    if hasattr(page, "set_icon"):                      # new API, accepts Gio.Icon
        page.set_icon(Gio.ThemedIcon.new("input-keyboard-symbolic"))

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

    # close_btn = Gtk.Button()
    # close_btn.set_icon_name("window-close-symbolic")
    # close_btn.add_css_class("flat")
    # close_btn.set_tooltip_text("Close search")
    # close_btn.connect("clicked", lambda btn: self.search_entry.set_text(""))
    # search_bar.append(close_btn)

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
        # kick off a background scan so the UI never blocks
        self._spawn_scan_thread(self.search_entry.get_text().strip())
        # … then move the keyboard focus into the top search box
        GLib.idle_add(self.search_entry.grab_focus)

def _on_reset_clicked(self, button):
    self._remove_all_files() 
    self._reset_btn()
    self.reset_btn.set_visible(False)
    self.add_more_button.set_visible(True)

def _on_dnd_drop(self, drop_target, value, x, y):
    if self.stack.get_visible_child_name() != "transcribe":
        return False

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
    file_row.set_subtitle(_hp(os.path.dirname(file_path)) or "Local File")

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

    output_buffer = GtkSource.Buffer() 
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
        'is_viewed': False,
        'transcript_path': None,
    }
    self.progress_items.append(file_data)

    file_row.set_activatable(True)
    file_row.connect('activated', lambda r: self._show_file_content(file_data) if file_data['status'] == 'completed' else self.show_file_details(file_data))
    self.files_group.add(file_row)
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
    if not self.progress_items:
        self._show_no_files_message()

def _show_no_files_message(self):
    pass

def show_file_details(self, file_data):
    return

def _show_text_buffer_window(
        self,
        title: str,
        src_buffer: Gtk.TextBuffer,
        initial_search: str | None = None
    ) -> None:
    """
    Show a read-only, searchable view of src_buffer in an overlay
    stacked above the main UI.  Requires that self.content_overlay
    (a Gtk.Overlay) was created once when the window was built.
    """

    # print("\n===== _show_text_buffer_window BEGIN =====")

    # ───────────────────────────────────────────────────────────
    # 1.  The root overlay that *does* have add_overlay()
    # ───────────────────────────────────────────────────────────
    overlay_root: Gtk.Overlay = self.content_overlay

    # ── 2½. Backdrop that blocks interaction & dims UI ───────────
    if getattr(self, "_backdrop_overlay", None):
        overlay_root.remove_overlay(self._backdrop_overlay)

    backdrop = Gtk.Box()               # just an empty, full‑size widget
    backdrop.set_hexpand(True)
    backdrop.set_vexpand(True)
    backdrop.set_name("overlay_backdrop")

    overlay_root.add_overlay(backdrop)         # ← add *before* the viewer
    self._backdrop_overlay = backdrop

    # ───────────────────────────────────────────────────────────
    # 2.  Remove any previous viewer overlay
    # ───────────────────────────────────────────────────────────
    if getattr(self, "_textbuf_overlay", None):
        print("[DBG] Removing previous viewer overlay")
        overlay_root.remove_overlay(self._textbuf_overlay)
        self._textbuf_overlay = None

    # ───────────────────────────────────────────────────────────
    # 3.  Build a fresh overlay box
    # ───────────────────────────────────────────────────────────
    viewer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    viewer.set_hexpand(True)
    viewer.set_vexpand(True)
    viewer.set_margin_top(50)
    viewer.add_css_class("card")
    viewer.add_css_class("boxed-list")

    viewer.set_name("overlay_viewer")           # unique CSS id

    css = """
    #overlay_viewer {
        background-image: none;                 /* no gradients */
        background-color: @window_bg_color;     /* solid, theme‑aware */
    }
    """
    prov = Gtk.CssProvider(); prov.load_from_data(css)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        prov,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    # Keep reference and attach
    self._textbuf_overlay = viewer
    overlay_root.add_overlay(viewer)

    # ───────────────────────── Header bar with close button ───
    tv = Adw.ToolbarView()
    hb = Adw.HeaderBar()
    hb.set_show_end_title_buttons(False)
    hb.set_title_widget(Adw.WindowTitle(title=title))

    close_btn = Gtk.Button(icon_name="window-close-symbolic")
    close_btn.add_css_class("flat")
    def _dismiss_viewer():
        nonlocal debounce_id
        if debounce_id is not None:
            GLib.source_remove(debounce_id)
            debounce_id = None

        # 1) viewer (text overlay)
        if self._textbuf_overlay and self._textbuf_overlay.get_parent():
            overlay_root.remove_overlay(self._textbuf_overlay)
            self._textbuf_overlay = None

        # 2) dark backdrop
        if self._backdrop_overlay and self._backdrop_overlay.get_parent():
            overlay_root.remove_overlay(self._backdrop_overlay)
            self._backdrop_overlay = None


    close_btn.connect("clicked", lambda *_: _dismiss_viewer())
    # Optional: clicking the dark backdrop also closes it
    click = Gtk.GestureClick()
    click.connect("pressed", lambda *_: _dismiss_viewer())
    backdrop.add_controller(click)

    hb.pack_end(close_btn)
    tv.add_top_bar(hb)

    # ───────────────────────── Main content area ───────────────
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    outer.set_margin_top(0)
    outer.set_margin_bottom(20)
    outer.set_margin_start(20)
    outer.set_margin_end(20)
    tv.set_content(outer)

    tvw = GtkSource.View.new_with_buffer(src_buffer)
    tvw.set_show_line_numbers(True)
    tvw.set_editable(False)
    tvw.set_monospace(True)
    tvw.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

    # ▾▾ NEW ▾▾  – don’t keep an undo stack for this read‑only view
    if isinstance(src_buffer, GtkSource.Buffer):
        if hasattr(src_buffer, "set_undo_manager"):          # GTK ≥ 4.10
            src_buffer.set_undo_manager(None)
        elif hasattr(src_buffer, "set_max_undo_levels"):     # older GTK
            src_buffer.set_max_undo_levels(0)

    # ── Theme‑aware colours for GtkSourceView ───────────────────────
    style_mgr  = Adw.StyleManager.get_default()
    scheme_mgr = GtkSource.StyleSchemeManager.get_default()

    def _apply_scheme(_mgr=None, _param=None):
        dark = style_mgr.get_dark()
        # “oblivion” ships with GtkSource and is dark; “classic” is light.
        scheme_id = "classic-dark" if dark else "classic"
        scheme = scheme_mgr.get_scheme(scheme_id)
        if scheme:                              # paranoia – both exist by default
            "\n".join(dir(src_buffer))  # debug
            src_buffer.set_style_scheme(scheme)

    _apply_scheme()                             # initial pick
    style_mgr.connect("notify::dark", _apply_scheme)  # live updates

    # initial highlight
    # ── after tvw is created ───────────────────────────────────────
    matches: list[tuple[Gtk.TextIter, Gtk.TextIter]] = []
    current = -1                        # -1 = “no match yet”

    # needed by _dismiss_viewer() and _queued_highlight()
    debounce_id: GLib.Source | None = None

    # ── helper ───────────────────────────────────────────────────
    def _apply_current_tag():
        buf = tvw.get_buffer()                            # get it every call
        buf.remove_tag_by_name("current",
                               buf.get_start_iter(), buf.get_end_iter())
        if matches and current >= 0:
            s, e = matches[current]
            buf.apply_tag_by_name("current", s, e)

    def _update_counter():
        counter_lbl.set_label(
            f"{current+1 if matches else 0} of {len(matches)}"
        )
        # enable / disable nav buttons
        up_btn.set_sensitive(len(matches) > 1)
        dn_btn.set_sensitive(len(matches) > 1)

    def _scroll_to(idx: int):
        """Scroll TextView so match *idx* is visible."""
        if not matches:
            return
        start, _end = matches[idx]
        tvw.scroll_to_iter(start, 0.10,  False, 0.0, 0.0)  # within‑margin 10 %

    def _highlight_and_collect(query: str):
        nonlocal matches, current
        matches = []

        buf        = tvw.get_buffer()
        start_iter = buf.get_start_iter()
        end_iter   = buf.get_end_iter()

        # create‑once‑and‑reuse
        tag_table = buf.get_tag_table()
        highlight_tag = tag_table.lookup("highlight") \
                        or buf.create_tag("highlight",
                                          background="#fff59d")
        current_tag   = tag_table.lookup("current")   \
                        or buf.create_tag("current",
                                          background="#ffca28")

        # clear old ranges  (no undo – faster, no memory growth)
        if hasattr(buf, "begin_irreversible_action"):
            buf.begin_irreversible_action()          # GTK 4.10+
        else:                                        # GTK ≤4.8
            buf.begin_not_undoable_action()

        buf.remove_tag(highlight_tag, start_iter, end_iter)
        buf.remove_tag(current_tag,   start_iter, end_iter)

        if not query:
            current = -1
            _update_counter()
            return

        flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
        # 2. walk the buffer using the C‑side search
        buf.begin_user_action() 
        it = start_iter.copy()
        while True:
            res = it.forward_search(query, flags, end_iter)
            if not res:
                break
            m_start, m_end = res
            matches.append((m_start, m_end))
            buf.apply_tag(highlight_tag, m_start, m_end)
            it = m_end          # continue *after* the match

        current = 0 if matches else -1
        _update_counter()
        _apply_current_tag()
        buf.end_user_action()  
        if current != -1:
            _scroll_to(current)



    def _jump(delta: int):
        """Move ±1 through matches and scroll."""
        nonlocal current
        if not matches:
            return
        current = (current + delta) % len(matches)
        _update_counter()
        _apply_current_tag()  
        _scroll_to(current)

    # ── search bar (entry  +  right‑side controls) ────────────────
    search = Gtk.SearchEntry()
    search.set_placeholder_text("Search…")
    search.set_hexpand(True)

    # give it focus as soon as the overlay is on screen
    GLib.idle_add(search.grab_focus)

    counter_lbl = Gtk.Label(label="0 of 0")
    dn_btn      = Gtk.Button(icon_name="go-down-symbolic"); dn_btn.add_css_class("flat")
    up_btn      = Gtk.Button(icon_name="go-up-symbolic");   up_btn.add_css_class("flat")
    clear_btn   = Gtk.Button(icon_name="edit-clear-symbolic"); clear_btn.add_css_class("flat")

    dn_btn.connect("clicked", lambda *_: _jump(+1))
    up_btn.connect("clicked", lambda *_: _jump(-1))
    # clear_btn.connect("clicked", lambda *_: search.set_text(""))

    # right after you create search and before the connect() lines
    debounce_id = None

    def _queued_highlight(entry):
        nonlocal debounce_id
        # cancel any still‑pending run
        if debounce_id:
            GLib.source_remove(debounce_id)
        query = entry.get_text().strip()

        # run after a short lull, in the GTK main loop
        def _run():
            nonlocal debounce_id
            _highlight_and_collect(query)
            debounce_id = None
            return False                     # one‑shot
        debounce_id = GLib.timeout_add(120, _run)  # 120 ms


    # react to typing *and* Enter
    search.connect("search-changed", _queued_highlight)
    search.connect("activate",       lambda *_: _jump(+1))

    controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    for w in (counter_lbl, up_btn, dn_btn):
        controls.append(w)

    search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    search_box.append(search)
    search_box.append(controls)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.set_hexpand(True)
    scroller.set_vexpand(True)
    scroller.set_child(tvw)

    # replace previous “box.append(search)” line with:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.append(search_box)
    box.append(scroller)
    outer.append(box)

    tv.set_hexpand(True)
    tv.set_vexpand(True)
    viewer.append(tv)

    # ───────────────────────── Final show ──────────────────────
    viewer.show()
    tv.show()

def _show_file_content(self, file_data):
    buf = file_data.get('buffer')

    if buf is None or buf.get_char_count() == 0:
        # try to reload from the saved text file
        dest = file_data.get('transcript_path')
        if not dest:
            # derive it from the original audio filename
            out_dir = getattr(self, 'output_directory',
                              os.path.expanduser("~/Downloads"))
            base = os.path.splitext(os.path.basename(file_data['path']))[0] + "_transcribed.txt"
            dest = os.path.join(out_dir, base)

        if not os.path.isfile(dest):
            self._error("No transcription content available.")
            return

        # rebuild a fresh buffer from disk
        import gi
        gi.require_version("GtkSource", "5")
        from gi.repository import GtkSource
        buf = GtkSource.Buffer()
        self._ensure_highlight_tag(buf)
        try:
            with open(dest, "r", encoding="utf-8") as fh:
                buf.set_text(fh.read())
            file_data['buffer'] = buf      # cache for next time
        except Exception as e:
            self._error(f"Failed to load transcript: {e}")
            return

    self._show_text_buffer_window(file_data['filename'], buf)


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

    # ── tag for the active match ─────────────────────
    cur_tag = tag_table.lookup("current")
    if cur_tag is None:
        cur_tag = buffer.create_tag("current")

    light_cur_color = Gdk.RGBA();
    light_cur_color.parse("#61ff90")  #  80 % L*
    dark_cur_color  = Gdk.RGBA();
    dark_cur_color.parse("#00775D")   #  50 % L*
    cur_tag.set_property("background-rgba", dark_cur_color if dark else light_cur_color)

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
    start, end = buf.get_start_iter(), buf.get_end_iter()
    for tag in ("highlight", "current"):
        buf.remove_tag_by_name(tag, start, end)
    if not search_text:
        return

    flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
    it = start.copy()
    while True:
        hit = it.forward_search(search_text, flags, end)
        if not hit:
            break
        m_start, m_end = hit
        buf.apply_tag_by_name("highlight", m_start, m_end)
        it = m_end

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

def _green(self, b):
    b.add_css_class("suggested-action")
    b.remove_css_class("destructive-action")

def _red(self, b):
    b.add_css_class("destructive-action")
    b.remove_css_class("suggested-action")

def _gui_status(self, msg):
    GLib.idle_add(self.status_lbl.set_label, msg)

def _reset_btn(self):
    self.trans_btn.set_visible(True)  
    self.trans_btn.set_label("Transcribe")
    self._green(self.trans_btn)
    self.trans_btn.set_sensitive(self._update_model_btn())
    # hide progress text, restore button
    if hasattr(self, "progress_lbl"):
        self.progress_lbl.set_visible(False)
        self.progress_lbl.set_label("")
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
    toast = Adw.Toast(title=msg)
    toast.set_timeout(5)
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

def _build_ui(self):
    self.toast_overlay = Adw.ToastOverlay()
    main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    main_box.set_margin_start(8)
    main_box.set_margin_end(8)
    main_box.set_margin_top(8)
    main_box.set_margin_bottom(8)
    # ─────────────────────────────────────────────────────────────
    # NEW: Wrap main_box in Gtk.Overlay and keep it for overlays
    self.content_overlay = Gtk.Overlay()
    self.content_overlay.set_child(main_box)
    self.content_overlay.set_hexpand(True)
    self.content_overlay.set_vexpand(True)
    # ─────────────────────────────────────────────────────────────

    # Place the overlay inside the toast overlay
    self.toast_overlay.set_child(self.content_overlay)

    # Attach the toast overlay to the window
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

    # model_box.append(Gtk.Label(label=" ("))
    self.status_lbl = Gtk.Label(label="Idle")          # <- status now lives here
    # model_box.append(self.status_lbl)
    # model_box.append(Gtk.Label(label=")"))

    main_box.append(model_box)

    # Line 2 : Output Directory          [Settings]
    output_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    output_box.set_halign(Gtk.Align.START)

    output_box.append(Gtk.Label(label="Output Directory: "))
    self.output_value_label = Gtk.Label(
        label= _hp(self.output_directory) or "Not set"
    )
    output_box.append(self.output_value_label)

    main_box.append(output_box)

    self._refresh_model_menu()
    self._update_model_btn()

def _browse_out_settings(self, button):
    dialog = Gtk.FileDialog()
    dialog.set_title("Select Output Directory")
    dialog.set_accept_label("Select")
    dialog.select_folder(self.window, None, self._on_browse_out_response)

def _on_browse_out_response(self, dialog, result):
    try:
        folder = dialog.select_folder_finish(result)
        if folder:
            self.output_directory = folder.get_path()
            if self.output_settings_row:
                self.output_settings_row.set_subtitle(_hp(self.output_directory))
                self.save_settings()
            if self.output_value_label:
                self.output_value_label.set_label(_hp(self.output_directory))

            # ── NEW: refresh Transcripts pane right away ────────────────
            scan_text = self.search_entry.get_text().strip() if self.search_entry else ""
            # this spawns the background scan thread and rebuilds rows
            self._spawn_scan_thread(scan_text)
    except GLib.Error:
        pass

def _setup_dnd(self):
    # (a) keep the old target on the files list so the cursor changes
    list_target = Gtk.DropTarget.new(type=Gdk.FileList,
                                     actions=Gdk.DragAction.COPY)
    list_target.connect("drop", self._on_dnd_drop)
    self.files_group.add_controller(list_target)

    # (b) NEW: accept drops anywhere on the window
    win_target = Gtk.DropTarget.new(type=Gdk.FileList,
                                    actions=Gdk.DragAction.COPY)
    win_target.connect("drop", self._on_window_dnd_drop)
    self.window.add_controller(win_target)

def _on_window_dnd_drop(self, drop_target, value, x, y):
    # Accept only while the Transcriber view is visible
    if self.stack.get_visible_child_name() != "transcribe":
        return False                         # let other handlers ignore it
    # Re‑use the original drop logic
    return self._on_dnd_drop(drop_target, value, x, y)


import gi, os, subprocess, threading
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gio, Gdk

class WhisperWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Audio-To-Text Transcriber")
        self.set_default_size(700, 580); self.set_border_width(8)

        # honour GNOME light/dark preference
        if Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme") == "prefer-dark":
            Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

        # ─── paths / state ────────────────────────────────────────────────────
        sd = os.path.abspath(os.path.dirname(__file__))
        self.bin_path   = os.path.join(sd, "whisper-cli")
        self.models_dir = os.path.join(sd, "models")
        self.display_to_file = {}
        self.cancel_flag = False
        self.current_proc = None

        # ─── layout ──────────────────────────────────────────────────────────
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); self.add(box)
        self._add_model_selector(box)
        self._add_audio_selector(box)
        self._add_output_selector(box)

        self.ts_check = Gtk.CheckButton(label="Include timestamps")
        box.pack_start(self.ts_check, False, False, 0)

        self.trans_btn = Gtk.Button(label="Transcribe"); self._green(self.trans_btn)
        self.trans_btn.connect("clicked", self.on_transcribe); box.pack_start(self.trans_btn, False, False, 6)

        self.status_lbl = Gtk.Label(label="Idle"); box.pack_start(self.status_lbl, False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)       # ← let tabs overflow nicely
        self.notebook.set_hexpand(True)
        self.notebook.set_vexpand(True)
        box.pack_start(self.notebook, True, True, 0)

        self.connect("destroy", Gtk.main_quit)

        self._setup_dnd() 

    # ────────────────────────────────────────────────────────────────────────
    #  Drag-and-drop helpers
    # ────────────────────────────────────────────────────────────────────────
    def _setup_dnd(self):
        targets = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]

        # 1) Whole window — accepts drops but shows **no** highlight
        self.drag_dest_set(Gtk.DestDefaults.MOTION | Gtk.DestDefaults.DROP,
                           targets, Gdk.DragAction.COPY)
        self.connect("drag-data-received", self.on_drag_data_received)

        # 2) Audio list & its scrolled window — full highlight
        self.audio_view.drag_dest_set(Gtk.DestDefaults.ALL,
                                      targets, Gdk.DragAction.COPY)
        self.audio_view.connect("drag-data-received", self.on_drag_data_received)

        scr = self.audio_view.get_parent()        # the ScrolledWindow
        scr.drag_dest_set(Gtk.DestDefaults.ALL,
                          targets, Gdk.DragAction.COPY)
        scr.connect("drag-data-received", self.on_drag_data_received)

    def on_drag_data_received(self, widget, drag_context, x, y,
                              selection, info, timestamp):
        uris = selection.get_data().decode().strip().splitlines()
        exts = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus")
        for uri in uris:
            if uri.startswith("file://"):
                try:
                    path, _ = GLib.filename_from_uri(uri)
                except Exception:
                    continue
                if path.lower().endswith(exts) and not any(r[0] == path for r in self.audio_store):
                    self.audio_store.append((path,))
        drag_context.finish(True, False, timestamp)

    # ── helper widgets ──────────────────────────────────────────────────────
    def _add_model_selector(self, parent):
        parent.pack_start(Gtk.Label(label="Model:"), False, False, 0)
        self.model_combo = Gtk.ComboBoxText(); self._populate_models()
        parent.pack_start(self.model_combo, False, False, 0)

    def _add_audio_selector(self, parent):
        parent.pack_start(Gtk.Label(label="Audio files:"), False, False, 0)
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        parent.pack_start(h, True, True, 0)

        self.audio_store = Gtk.ListStore(str)

        tv = Gtk.TreeView(model=self.audio_store)
        tv.append_column(Gtk.TreeViewColumn("Path", Gtk.CellRendererText(), text=0))

        # ── NEW: enable multi-row selection ────────────────────────────────
        tv.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)

        # ── NEW: listen for Delete / Backspace ─────────────────────────────
        tv.connect("key-press-event", self.on_audio_key_press)

        scr = Gtk.ScrolledWindow(); scr.set_vexpand(True); scr.add(tv)
        h.pack_start(scr, True, True, 0)

        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        h.pack_start(vb, False, False, 0)
        add = Gtk.Button(label="Add Audio…"); add.connect("clicked", self.on_add_audio)
        vb.pack_start(add, False, False, 0)
        rem = Gtk.Button(label="Remove Selected"); rem.connect("clicked", self.on_remove_audio)
        vb.pack_start(rem, False, False, 0)

        self.audio_view = tv                     # keep for d-n-d & key-handler

    # ── NEW: delete rows with keyboard ──────────────────────────────────────
    def on_audio_key_press(self, widget, event):
        if event.keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self.on_remove_audio(None)
            return True      # stop further processing of the key
        if (event.keyval in (Gdk.KEY_a, Gdk.KEY_A)) and (event.state & Gdk.ModifierType.CONTROL_MASK):
            sel.select_all();            return True
        return False          # propagate other keys

    def _add_output_selector(self, parent):
        parent.pack_start(Gtk.Label(label="Output folder:"), False, False, 0)
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.out_entry = Gtk.Entry()
        self.out_entry.set_editable(False); self.out_entry.set_can_focus(False)
        h.pack_start(self.out_entry, True, True, 0)

        b = Gtk.Button(label="Browse…"); b.connect("clicked", self.on_browse_out)
        h.pack_start(b, False, False, 0)
        parent.pack_start(h, False, False, 0)

        downloads = os.path.expanduser("~/Downloads")
        if os.path.isdir(downloads):
            self.out_entry.set_text(downloads)

    # ── colours for the main button ─────────────────────────────────────────
    def _green(self, b):
        ctx = b.get_style_context()
        ctx.remove_class("destructive-action")
        ctx.add_class("suggested-action")

    def _red(self, b):
        ctx = b.get_style_context()
        ctx.remove_class("suggested-action")
        ctx.add_class("destructive-action")

    # ── models dropdown ─────────────────────────────────────────────────────
    def _populate_models(self):
        size = {"tiny": "Smallest", "base": "Smaller", "small": "Small",
                "medium": "Medium", "large": "Large", "xl": "XL"}
        lang = {"en": "English", "fr": "French", "es": "Spanish", "de": "German"}

        # 1) collect all the ggml-*.bin files
        try:
            files = [f for f in os.listdir(self.models_dir)
                     if f.startswith("ggml-") and f.endswith(".bin")]
        except FileNotFoundError:
            files = []

        # 2) desired display order
        desired_order = [
            "tiny.en", "tiny",
            "base.en", "base",
            "small.en", "small",
            "medium.en", "medium",
            "large.en", "large",
            "xl.en", "xl"
        ]

        def sort_key(fn):
            core = fn[len("ggml-"):-4]
            return desired_order.index(core) \
                if core in desired_order else len(desired_order)

        files.sort(key=sort_key)

        if not files:
            self.model_combo.append_text("<no models found>")
            self.model_combo.set_active(0)
            return

        for f in files:
            core = f[len("ggml-"):-4]
            size_key, lang_key = (core.split(".", 1) + [None])[:2]
            if lang_key:
                label = f"{size.get(size_key, size_key.title())} " \
                        f"{lang.get(lang_key, lang_key.upper())} Model"
            else:
                label = f"{size.get(size_key, size_key.title())} Model"
            self.display_to_file[label] = f
            self.model_combo.append_text(label)

        self.model_combo.set_active(0)

    # ── UI‑thread helpers ───────────────────────────────────────────────────
    def _gui_log(self, buf, text):
        def _append():
            buf.insert(buf.get_end_iter(), text + "\n"); return False
        GLib.idle_add(_append)

    def _gui_status(self, msg): GLib.idle_add(self.status_lbl.set_text, msg)
    def _gui_tab_title(self, lbl, txt): GLib.idle_add(lbl.set_text, txt)

    # ── “+ Audio” / “Remove” / folder browse ────────────────────────────────
    def on_add_audio(self, _):
        dlg = Gtk.FileChooserDialog("Select audio files", self, Gtk.FileChooserAction.OPEN,
                                    (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK))
        dlg.set_select_multiple(True)
        f = Gtk.FileFilter(); f.set_name("Audio"); f.add_pattern("*.mp3"); f.add_pattern("*.wav"); dlg.add_filter(f)
        if dlg.run() == Gtk.ResponseType.OK:
            for fn in dlg.get_filenames():
                if not any(r[0]==fn for r in self.audio_store):
                    self.audio_store.append((fn,))
        dlg.destroy()

    def on_remove_audio(self, _):
        model, paths = self.audio_view.get_selection().get_selected_rows()
        for p in reversed(paths): model.remove(model.get_iter(p))

    def on_browse_out(self, _):
        dlg = Gtk.FileChooserDialog("Select folder", self, Gtk.FileChooserAction.SELECT_FOLDER,
                                    (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.OK))
        if dlg.run() == Gtk.ResponseType.OK:
            self.out_entry.set_text(dlg.get_filename())
        dlg.destroy()

    # ── start / cancel button ───────────────────────────────────────────────
    def on_transcribe(self, _):
        if self.trans_btn.get_label() == "Cancel":
            self.cancel_flag = True
            if self.current_proc:  # stop current CLI gracefully
                try: self.current_proc.terminate()
                except: pass
            self._gui_status("Cancelling…")
            return
        else:
            n = self.notebook.get_n_pages()
            for i in reversed(range(n)):
                self.notebook.remove_page(i)

        # sanity checks
        model_disp = self.model_combo.get_active_text()
        model_fn   = self.display_to_file.get(model_disp, "")
        model_path = os.path.join(self.models_dir, model_fn)
        files      = [r[0] for r in self.audio_store]
        out_dir    = self.out_entry.get_text().strip() or None

        if not os.path.isfile(self.bin_path):
            return self._error(f"whisper-cli not found:\n{self.bin_path}")
        if not os.path.isfile(model_path):
            return self._error(f"Model not found:\n{model_path}")
        if not files:
            return self._error("No audio files selected.")
        if not out_dir:
            return self._error("Please choose an output folder first.")
        
        # switch to “Cancel” mode
        self.cancel_flag = False; self.trans_btn.set_label("Cancel"); self._red(self.trans_btn)

        threading.Thread(target=self._worker,
                         args=(model_path, files, out_dir), daemon=True).start()

    # ── background worker thread ────────────────────────────────────────────
    def _worker(self, model_path, files, out_dir):
        total = len(files)
        for idx, path in enumerate(files, 1):
            if self.cancel_flag: break
            name = os.path.basename(path)
            self._gui_status(f"{idx}/{total} – {name}")

            # create per‑file tab (must run on GTK thread)
            buf_holder = {}; lbl_holder = {}
            def _make_tab():
                buf = Gtk.TextBuffer()
                view = Gtk.TextView(buffer=buf, editable=False, monospace=True)
                scr = Gtk.ScrolledWindow(); scr.add(view); scr.show_all()
                lbl = Gtk.Label(label=f"⏳ {name}")
                self.notebook.append_page(scr, lbl)
                self.notebook.set_current_page(-1)
                buf_holder['buf'] = buf
                lbl_holder['lbl'] = lbl
            GLib.idle_add(_make_tab, priority=GLib.PRIORITY_HIGH_IDLE)

            # wait until GTK created the widgets
            while not buf_holder: pass
            buf = buf_holder['buf']; lbl = lbl_holder['lbl']

            # run CLI
            cmd = [self.bin_path, "-m", model_path, "-f", path]
            if not self.ts_check.get_active(): cmd.append("-nt")
            self._gui_log(buf, "transcribing …")
            self.current_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE, text=True, bufsize=1)

            for line in self.current_proc.stdout:
                if self.cancel_flag:
                    try: self.current_proc.terminate()
                    except: pass
                    GLib.idle_add(self._gui_tab_title, lbl, f"❌ {name}")
                    break
                self._gui_log(buf, line.rstrip())
            self.current_proc.stdout.close()
            self.current_proc.wait()

            if self.cancel_flag: 
                GLib.idle_add(self._gui_tab_title, lbl, f"❌ {name}")
                break

            if self.current_proc.returncode != 0:
                err = self.current_proc.stderr.read().strip()
                self.current_proc.stderr.close()
                self._gui_log(buf, f"ERROR: {err}")
            else:
                self.current_proc.stderr.close()
                dest = os.path.join(out_dir, os.path.splitext(name)[0] + ".txt")

                def _save():
                    text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
                    with open(dest, "w", encoding="utf-8") as f: f.write(text)
                    buf.insert(buf.get_end_iter(), f"\nSaved → {dest}\n")
                    return False
                GLib.idle_add(_save)

            # self._gui_tab_title(lbl, f"✅ {name}")
            GLib.idle_add(self._gui_tab_title, lbl, f"✅ {name}")

        self._gui_status("Done" if not self.cancel_flag else "Cancelled")
        GLib.idle_add(self._reset_btn)

    def _reset_btn(self):
        self.trans_btn.set_label("Transcribe"); self._green(self.trans_btn)

    # ── simple error dialog ─────────────────────────────────────────────────
    def _error(self, msg):
        dlg = Gtk.MessageDialog(self, Gtk.DialogFlags.MODAL, Gtk.MessageType.ERROR,
                                Gtk.ButtonsType.CLOSE, msg)
        dlg.run(); dlg.destroy()

# ───────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    WhisperWindow().show_all(); Gtk.main()

import gi, os, subprocess, threading, shlex
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gio, Gdk

class WhisperWindow(Gtk.Window):
    # ───────────────────────── initialisation ──────────────────────────────
    def __init__(self):
        super().__init__(title="Audio-To-Text Transcriber")
        self.set_default_size(700, 580); self.set_border_width(8)
        if Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme") == "prefer-dark":
            Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

        # paths / state
        sd = os.path.abspath(os.path.dirname(__file__))
        self.bin_path        = os.path.join(sd, "whisper-cli")
        self.models_dir      = os.path.join(sd, "models")
        self.download_script = os.path.join(sd, "download-ggml-model.sh")
        os.makedirs(self.models_dir, exist_ok=True)

        self.display_to_core = {}      # UI label  →  model core name (e.g. "tiny.en")
        self.cancel_flag     = False
        self.current_proc    = None

        # layout
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); self.add(box)
        self._add_model_selector(box)
        self._add_audio_selector(box)
        self._add_output_selector(box)

        self.ts_check   = Gtk.CheckButton(label="Include timestamps");   box.pack_start(self.ts_check, False, False, 0)
        self.trans_btn  = Gtk.Button(label="Transcribe"); self._green(self.trans_btn)
        self.trans_btn.connect("clicked", self.on_transcribe);           box.pack_start(self.trans_btn, False, False, 6)
        self.status_lbl = Gtk.Label(label="Idle");                       box.pack_start(self.status_lbl, False, False, 0)

        self.notebook   = Gtk.Notebook(); self.notebook.set_scrollable(True)
        self.notebook.set_hexpand(True); self.notebook.set_vexpand(True)
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

    # ───────────────────────── model selector ──────────────────────────────
    def _add_model_selector(self, parent):
        parent.pack_start(Gtk.Label(label="Model:"), False, False, 0)
        self.model_combo = Gtk.ComboBoxText()
        self._populate_models()
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

    # ─────────────────────── ensure-model-exists ───────────────────────────
    def _ensure_model(self, core):
        """
        Guarantee that ggml-<core>.bin is present in models_dir.
        Returns the full path on success, or None on failure / user abort.
        """
        target = os.path.join(self.models_dir, f"ggml-{core}.bin")
        if os.path.isfile(target):
            return target

        # ask user
        dlg = Gtk.MessageDialog(self, Gtk.DialogFlags.MODAL, Gtk.MessageType.QUESTION,
                                Gtk.ButtonsType.NONE,
                                f"The model “{core}” is not installed.\n"
                                f"Download it now (~few-hundred MB)?")
        dlg.add_button("Cancel",  Gtk.ResponseType.CANCEL)
        dlg.add_button("Download", Gtk.ResponseType.OK)
        if dlg.run() != Gtk.ResponseType.OK:
            dlg.destroy();   return None
        dlg.destroy()

        # download via the supplied shell script
        self._gui_status(f"Downloading model {core} …")
        self.trans_btn.set_sensitive(False)
        self.model_combo.set_sensitive(False)
        cmd = ["sh", self.download_script, core, self.models_dir]

        # run in blocking mode but keep UI alive with GLib
        def _download():
            proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for line in proc.stdout:
                GLib.idle_add(self.status_lbl.set_text, line.strip()[:120])
            proc.wait()
            GLib.idle_add(self.trans_btn.set_sensitive, True)
            GLib.idle_add(self.model_combo.set_sensitive, True)
            if proc.returncode == 0 and os.path.isfile(target):
                GLib.idle_add(self.status_lbl.set_text, "Download complete")
            else:
                GLib.idle_add(self._error, f"Failed to download model “{core}”.")
        threading.Thread(target=_download, daemon=True).start()

        # spin until file appears or user closes window / error
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        # simple wait loop (non-blocking UI because of above)
        while not os.path.isfile(target):
            if not Gtk.events_pending():
                GLib.usleep(100_000)   # 0.1 s
            else:
                Gtk.main_iteration_do(False)
        return target if os.path.isfile(target) else None

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
        """
        Fill the drop-down with *all* common Whisper models.
        If a model is missing locally the label gets a ‘(download)’ suffix.
        """
        size = {"tiny":"Smallest","base":"Smaller","small":"Small","medium":"Medium","large":"Large","xl":"XL"}
        lang = {"en":"English","fr":"French","es":"Spanish","de":"German"}

        desired = ["tiny.en","tiny","base.en","base","small.en","small",
                   "medium.en","medium","large-v3-turbo","large-v3","large-v2","large-v1"]

        for core in desired:
            fn   = f"ggml-{core}.bin"
            path = os.path.join(self.models_dir, fn)
            size_key, lang_key = (core.split(".",1)+[None])[:2]
            if lang_key:
                label = f"{size.get(size_key,size_key.title())} {lang.get(lang_key,lang_key.upper())} Model"
            else:
                label = f"{size.get(size_key,size_key.title())} Model"
            if not os.path.isfile(path):
                label += " (download)"
            self.model_combo.append_text(label)
            self.display_to_core[label] = core

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
            if self.current_proc:
                try: self.current_proc.terminate()
                except Exception: pass
            self._gui_status("Cancelling…")
            return

        # clear old tabs
        for i in reversed(range(self.notebook.get_n_pages())):
            self.notebook.remove_page(i)

        # gather selections
        model_disp = self.model_combo.get_active_text()
        core       = self.display_to_core.get(model_disp, "")
        model_path = self._ensure_model(core)
        if model_path is None:
            return                                # download failed or cancelled

        files   = [r[0] for r in self.audio_store]
        out_dir = self.out_entry.get_text().strip() or None
        if not files:   return self._error("No audio files selected.")
        if not out_dir: return self._error("Please choose an output folder first.")
        if not os.path.isfile(self.bin_path):
            return self._error(f"whisper-cli not found:\n{self.bin_path}")

        # switch to cancel mode
        self.cancel_flag = False; self.trans_btn.set_label("Cancel"); self._red(self.trans_btn)
        threading.Thread(target=self._worker, args=(model_path, files, out_dir), daemon=True).start()

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

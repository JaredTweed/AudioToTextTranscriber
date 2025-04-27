Below is **everything in one place**:

---

## 1 Source-tree layout on your workstation

```
Audio-To-Text-Transcriber/            ← project root
├── io.github.JaredTweed.AudioToTextTranscriber.yml   ← Flatpak manifest
├── wrapper.sh                        ← tiny launcher (executes python3 main.py)
├── src/
│   └── main.py                       ← the GUI you pasted
├── data/                             ← resources copied unchanged
│   ├── io.github.JaredTweed.AudioToTextTranscriber.desktop
│   └── icons/
│       └── io.github.JaredTweed.AudioToTextTranscriber.png  (128×128 PNG)
└── whisper.cpp/                      ← git clone https://github.com/ggml-org/whisper.cpp.git
    └── models/download-ggml-model.sh
```

---

## 2 What ends up **inside the Flatpak sandbox**

```
/app
├── bin/
│   └── audio-to-text-transcriber              ← wrapper.sh
└── share/
    ├── audio-to-text-transcriber/
    │   ├── main.py
    │   ├── models/download-ggml-model.sh
    │   └── whisper.cpp/build/bin/whisper-cli  ← built in Release mode
    ├── applications/
    │   └── io.github.JaredTweed.AudioToTextTranscriber.desktop
    └── icons/hicolor/128x128/apps/
        └── io.github.JaredTweed.AudioToTextTranscriber.png
```

Your Python code therefore finds:

* `../whisper.cpp/build/bin/whisper-cli`  
  and  
* `../whisper.cpp/models/download-ggml-model.sh`

exactly where it expects them.

---

## 3 Complete Flatpak manifest  
`io.github.JaredTweed.AudioToTextTranscriber.yml`

```yaml
id: io.github.JaredTweed.AudioToTextTranscriber

runtime: org.gnome.Platform
runtime-version: '48'
sdk: org.gnome.Sdk
command: audio-to-text-transcriber       # wrapper in /app/bin

# ── Sandbox permissions ────────────────────────────────────────────────
finish-args:
  - --socket=wayland
  - --socket=x11
  - --device=dri
  - --share=network                     # model downloads
  - --filesystem=home                   # user chooses any output folder

# ── Modules ────────────────────────────────────────────────────────────
modules:
  # 1 ▸ Build whisper.cpp (static, no tests/examples, Release)
  - name: whisper-cpp
    buildsystem: simple
    build-commands:
      # configure
      - cmake -S . -B build \
              -DCMAKE_BUILD_TYPE=Release \
              -DBUILD_SHARED_LIBS=OFF \
              -DWHISPER_BUILD_TESTS=OFF \
              -DWHISPER_BUILD_EXAMPLES=OFF
      # build
      - cmake --build build --config Release --parallel
      # copy whisper-cli into the path main.py expects
      - install -Dm755 build/bin/whisper-cli \
          /app/share/audio-to-text-transcriber/whisper.cpp/build/bin/whisper-cli
    sources:
      - type: git
        url: https://github.com/ggml-org/whisper.cpp.git
        tag: v1.7.5                  # or branch: master

  # 2 ▸ Install your Python app, wrapper, desktop file & icon
  - name: app
    buildsystem: simple
    build-commands:
      # main Python file
      - install -Dm644 src/main.py \
          /app/share/audio-to-text-transcriber/main.py

      # model-download script from the cloned repo
      - install -Dm755 ../whisper-cpp/models/download-ggml-model.sh \
          /app/share/audio-to-text-transcriber/models/download-ggml-model.sh

      # wrapper that starts the app
      - install -Dm755 wrapper.sh /app/bin/audio-to-text-transcriber

      # desktop entry & icon
      - install -Dm644 data/io.github.JaredTweed.AudioToTextTranscriber.desktop \
          /app/share/applications/io.github.JaredTweed.AudioToTextTranscriber.desktop
      - install -Dm644 data/icons/io.github.JaredTweed.AudioToTextTranscriber.png \
          /app/share/icons/hicolor/128x128/apps/io.github.JaredTweed.AudioToTextTranscriber.png
    sources:
      - type: dir    # Python source tree
        path: src
      - type: file   # wrapper
        path: wrapper.sh
      - type: dir    # desktop & icon
        path: data
```

### wrapper.sh (contents)

```bash
#!/usr/bin/env bash
exec python3 /app/share/audio-to-text-transcriber/main.py "$@"
```

Make it executable:

```bash
chmod +x wrapper.sh
```

---

### Build & run

```bash
flatpak-builder --user --install --force-clean build-dir \
    io.github.JaredTweed.AudioToTextTranscriber.yml

flatpak run io.github.JaredTweed.AudioToTextTranscriber
```

The GUI will open immediately; model downloads and transcriptions will work without any runtime compilation.
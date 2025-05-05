# Build
flatpak-builder --user --install --force-clean build-dir   io.github.JaredTweed.AudioToTextTranscriber.yml

# Run
flatpak run io.github.JaredTweed.AudioToTextTranscriber

# YML
Use this command at `whisper.cpp` to remove `stdc++fs`:  `find . -name CMakeLists.txt -exec sed -i '/stdc++fs/d' {} \;`

# sha256
```
curl -L https://github.com/JaredTweed/Audio-To-Text-Transcriber/releases/download/v1.0.1/v1.0.1.zip \
  | sha256sum
```
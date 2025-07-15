#!/bin/bash

# Get the absolute path of the script's directory
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# Ensure required runtimes are installed
echo "Installing required runtimes..."
flatpak install -y --system org.gnome.Sdk/x86_64/48 org.gnome.Platform/x86_64/48 

cd "$SCRIPT_DIR"

if [ "$1" = "bundle" ]; then
    echo "Building and bundling WineCharm..."
    flatpak-builder --force-clean build-dir io.github.JaredTweed.AudioToTextTranscriber.yml
    flatpak build-bundle repo AudioToTextTranscriber.flatpak io.github.JaredTweed.AudioToTextTranscriber
else
    echo "Building and running WineCharm..."
    flatpak-builder --install --user --force-clean build-dir packaging/io.github.JaredTweed.AudioToTextTranscriber.yml
    flatpak kill io.github.JaredTweed.AudioToTextTranscriber 2> /dev/null
    flatpak run io.github.JaredTweed.AudioToTextTranscriber
fi

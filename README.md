feel free to make edits and contributions via pull requests. this is open source.

Transcribes audio. I use it so i can search through my audio files quickly for work; either using ctrl+F in a text editor, or uploading the txt file to AI.

## Future things to add

* recording audio?
* Ctrl+F within the output.
* GTK 3.0 -> GTK 4.0

## Creating AppImage (in progress)
```
./build-appimage.sh 2>&1 | tee build.log
./Audio-to-Text_Transcriber-1.0.1-x86_64.AppImage
```

feel free to make edits and contributions via pull requests. this is **open source**.

Transcribes audio. I use it so i can search through my audio files quickly for work; either using ctrl+F in a text editor, or uploading the txt file to AI.

## How to run

Got to the flatpak folder, and run `./build.sh`.

## Future things to add/fix

* recording audio? (doesn't work at all for me, maybe it will be abandoned, we will decide later).
* Remove homepage because it add unnecessary complexity.
* Ctrl+F within the output.
* Show transcription as it is transcribing (likely will only work if the audio has timestamps).
* Error in selecting output directory from the home page rather from settings (you can open the file manager, but not select any folders).
* allow draging files into desired order.
* fix issue where icon is not displayed correctly in the about page.
* fix issue where the icon is not displayed correctly in the taskbar.
* Avoid retranscribing already transcribed files.
* have the smallest model installed by default


## Creating AppImage (in progress) (likely will switch to only flatpak if possible).
```
./build-appimage.sh 2>&1 | tee build.log
./Audio-to-Text_Transcriber-1.0.1-x86_64.AppImage
```

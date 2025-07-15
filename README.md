feel free to make edits and contributions via pull requests. this is **open source**.

Transcribes audio. I use it so i can search through my audio files quickly for work; either using ctrl+F in a text editor, or uploading the txt file to AI.

## How to run

Go to the src, and run `./build.sh`.

## Future things to add/fix

* allow draging files into desired order. (issue postponed until after flatpak is published on flathub).
* fix issue where icon is not displayed correctly in the about page.
* fix issue where the icon is not displayed correctly in the taskbar.
* have the smallest model installed by default


## Creating AppImage (in progress) (likely will switch to only flatpak if possible).
```
./build-appimage.sh 2>&1 | tee build.log
./Audio-to-Text_Transcriber-1.0.1-x86_64.AppImage
```

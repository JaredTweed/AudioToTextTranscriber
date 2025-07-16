<p align="center"><img width='190' src="https://github.com/JaredTweed/AudioToTextTranscriber/blob/main/images/icon-large.png">
<h1 align="center">Audio-To-Text Transcriber</h1>

<p align="center">Audio-To-Text Transcriber is a <a href="https://github.com/ggml-org/whisper.cpp">whisper.cpp</a> GUI app which allows you to locally transcribe audio files so that you can easily search through them.</p>

<p align="center">This is app is completely open source, so please contribute and make it better! Also, I love getting stars on github :)</p>

<!--<p align="center"><a href='https://flathub.org/apps/io.github.JaredTweed.AudioToTextTranscriber'><img width='190' alt='Download on Flathub' src='https://flathub.org/api/badge?locale=en'/></a></p> -->

## How to run

Run `./build.sh` from the root directory.

## Where to get audio to test this

https://commons.wikimedia.org/wiki/Category:Audio_files_of_speeches_in_English

## Future things to add/fix

### Postponed until after flatpak release

* allow draging files into desired order. (issue postponed until after flatpak is published on flathub).
* have the smallest model installed by default
* Move tabs to where the title is currently. The title does not need to be in the app. Or alternatively, make it so that the tabs have the same margins in the window as the rows beneath. or something else we will brainstorm...

### Intended to complete before flatpak release

* Starting Transcribing should replace "add audio files" button with transcription progress info e.g., "34% \(~3:26\)" or "Transcribing..."
* Reload button after transcription finishes should clear up the list. And the transcribe button should be disabled after transcription.
* Show an estimate somewhere for the time until completion.

<p align="center"><img width='190' src="https://github.com/JaredTweed/AudioToTextTranscriber/blob/main/images/icon-large.png">
<h1 align="center">Audio-To-Text Transcriber</h1>

<p align="center">Audio-To-Text Transcriber is a <a href="https://github.com/ggml-org/whisper.cpp">whisper.cpp</a> GUI app which allows you to locally transcribe audio files so that you can easily search through them.</p>

<p align="center">This is app is completely open source, so please contribute and make it better! Also, I love getting stars on github :)</p>

<!--<p align="center"><a href='https://flathub.org/apps/io.github.JaredTweed.AudioToTextTranscriber'><img width='190' alt='Download on Flathub' src='https://flathub.org/api/badge?locale=en'/></a></p> -->

## How to run

Run `./build.sh` from the root directory.

If you want to build it quicker for less accurate testing (e.g.,the transcription won't work), run `python3 -m src.audio_to_text_transcriber.main` from the root directory.

## Where to get audio to test this

https://commons.wikimedia.org/wiki/Category:Audio_files_of_speeches_in_English

## Future things to add/fix

### Postponed until after flathub release

* allow draging files into desired order.
* have the smallest model installed by default
* Prevent freezing when opening transcriptions.

### Intended to complete before flathub release

We are ready for the flathub release!
* update flatpak xml so that it show the newest images of the app.

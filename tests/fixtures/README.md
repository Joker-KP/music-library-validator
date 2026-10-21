The tiny MP3 and FLAC fixtures contain synthetic silence (0.05 seconds), generated
with FFmpeg's `anullsrc=r=44100:cl=mono` source. Tags are `title=Łódź`,
`artist=Artysta`, `album=Żółć`, `track=1`, `comment=Artysta - Żółć`.
The MP3 additionally has an ID3 COMM frame written during fixture creation.
They contain no third-party music or artwork. Tests copy them into temporary
libraries; scanner code never writes them. FFmpeg is not needed to run tests.

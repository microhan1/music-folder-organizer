# Music Folder Organizer

<img src="assets/icon.png" width="96" alt="icon">

[한국어](README.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md)

> **Works without an internet connection.**
> **The default is move, not copy. Undo is always there.**

A Windows tool that takes music scattered across `Downloads/New folder/New folder (2)` and moves it into `Artist/Album/01 - Title.mp3` folders based on the tags (artist, album, track, title), and finds songs you have more than once.

![Preview: current path → new path](docs/screenshot.png)

## Download

- **Executable**: get `music-folder-organizer.exe` from [Releases](https://github.com/microhan1/music-folder-organizer/releases) and double-click it. Nothing to install. (The exe is unsigned; if SmartScreen warns, choose "More info → Run anyway".)
- **From source** (Python 3.12):

```bash
pip install -r requirements.txt
python main.py
```

## How to use

1. Drop a music folder on the window.
2. Check current path → new path in the preview. Untick files that should stay where they are.
3. Press **Run**. Not happy? **Undo…** lists the runs so you can pick one to put back (the newest is preselected). If a newer run moved the same files again, it tells you to undo that one first.

Nothing moves before you press Run. The line above the table always shows "Move n, unchanged n, missing tags n, duplicates n, empty folders n".

| Status | Meaning |
| --- | --- |
| OK | moves to the new path |
| No change | already in place |
| Missing tags | no title or no artist; left out by default (an option puts them under "Unknown Artist") |
| Duplicate | goes to the `_Duplicates` folder (or the recycle bin) |
| Conflict | two files want the same path; ` (2)` is added (yellow) |

## Folder pattern

Pick one of three presets or type your own.

- `{album_artist|artist}/{album}/{track:02} - {title}` (default)
- `{artist}/{title}`
- `{year}/{artist} - {title}`

| Placeholder | Value |
| --- | --- |
| `{artist}` | artist |
| `{album_artist}` | album artist |
| `{album}` | album |
| `{title}` | title (the original file name when empty) |
| `{track}`, `{track:02}` | track number (`:02` = two digits); the file name's leading number when the tag is empty |
| `{disc}` | disc number |
| `{year}` | year (four digits) |
| `{genre}` | genre |
| `{artist_sort}` | artist sort name (Latin-letter folders for Japanese or Chinese artists) |
| `{a\|b}` | b when a is empty |

Empty values become fallback names such as "Unknown Artist" and "Unknown Album" (change them with `fallbacks` in `settings.json`). Characters Windows does not allow in names (`<>:"/\|?*`) become `_`; a path longer than 240 characters is shortened, title first.

`.lrc` files, Music Tag Filler backups (`.tagbak.json`) and `cover.jpg` / `folder.jpg` move along with the music. Folders left empty are removed (including ones holding only `Thumbs.db` or `desktop.ini`).

**Album extras**: when every song of a folder goes to the same new folder, the album's `.cue`, `.log`, `.txt`, `.nfo`, `.m3u`, `.pdf` and image files go too, along with subfolders that hold only such files (`Artwork`, `Scans`, ...). If the songs split across albums, or for files outside that list (personal documents and the like), things stay where they are. Turn it off with the "Bring album extras along" option (`--no-sidecars`).

**Albums with a cue sheet**: when a folder's `.cue` names the music files in it, those files keep their names and only the folder follows the pattern, so the cue sheet stays valid (its content is not touched).

## Duplicates

| Stage | Rule |
| --- | --- |
| 1. Identical | same file content (SHA-1) |
| 2. Same song | same artist + title, lengths within 2 s |
| 3. Sound | acoustic fingerprints (Chromaprint, compared locally); finds the same recording even with different tags |

**Only extra copies within an album go.** A song that is on several albums (an original album and a best-of) is shown as one group but **keeps one file per album** (the group name says "n albums"). Within an album the lossless (flac) file wins, then the higher bitrate, then the one already in place. Change it with **Keep**, or untick **Apply** to leave a whole group alone. On the command line, `--dedupe-across-albums` keeps only one copy even across albums.

The others are not deleted; they move to the `_Duplicates` folder. The recycle bin is an option, but files sent there do not come back with this tool's Undo.

## Artist merging

`岡田 有希子`, `岡田有希子` and `Yukiko Okada` would make three folders. They are joined by:

1. the same MusicBrainz / iTunes artist ID
2. spacing, full-width and case differences, and a trailing bracket in another script (`Minako Yoshida (吉田美奈子)`)
3. `artists.json` — `{"岡田有希子": ["岡田 有希子", "Yukiko Okada", "오카다 유키코"]}` (the key is the folder name)
4. different artist spellings within one album in one folder: the tool asks "same artist?" (blue). Nothing changes until you confirm; confirming writes `artists.json`.

The **Artists** tab renames a merged artist or splits a group.

## Command line

```bash
python main.py ./music --dry-run                       # print the table, move nothing
python main.py ./music --dest ./sorted --copy          # copy into another folder
python main.py ./music --dedupe --fingerprint          # duplicates (by sound too) to _Duplicates
python main.py ./music --pattern "{artist}/{title}"
python main.py ./music --undo                          # undo the latest run
python main.py ./music --history                       # undo history, with run IDs
python main.py ./music --undo-run 1a2b3c4d5e6f         # undo one chosen run
```

`python main.py --help` lists the options in your OS language. Tests: `pip install -r requirements-dev.txt`, then `python samples/make_samples.py` and `python -m pytest tests`.

## With Music Tag Filler

If tags are empty, fill them first with [Music Tag Filler](https://github.com/microhan1/music-tag-filler).

- When some files lack tags, **Open in Music Tag Filler** appears at the right of the summary line. It opens those files (mp3, flac, m4a, ogg) there, and when that window closes the tags are read again here. Its location is asked for once and remembered (found automatically next to this program or in `music-tag-filler\dist` beside it).
- The MusicBrainz and iTunes artist IDs Music Tag Filler writes join different spellings of one artist into one folder.
- Its backups (`.tagbak.json`) move with the files, so its Undo still works after the folders are reorganized.
- **Export missing-tag list** writes those files to `untagged.txt`.

## What it does not do

- It never edits tags; it only reads them (editing is Music Tag Filler's job).
- It never connects to the internet. Fingerprints are compared locally.
- It never deletes files outright: duplicates go to the `_Duplicates` folder or the recycle bin.
- No format conversion, playback or playlists.
- Lyrics, covers and album extras only travel with the music; their content is never edited.

## License

- MIT License ([LICENSE](LICENSE))
- The bundled `third_party/fpcalc.exe` (Chromaprint, with parts of FFmpeg) is LGPL 2.1 ([third_party/LICENSE-chromaprint](third_party/LICENSE-chromaprint)).

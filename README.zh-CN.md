# 音乐文件夹整理 (Music Folder Organizer)

<img src="assets/icon.png" width="96" alt="icon">

[한국어](README.md) · [English](README.en.md) · [日本語](README.ja.md)

> **无需联网即可使用。**
> **默认是移动而不是复制，随时可以撤销。**

把散落在 `下载/新建文件夹/新建文件夹(2)` 里的音乐文件，按标签(歌手·专辑·音轨·标题)整理成 `歌手/专辑/01 - 标题.mp3` 结构，并找出重复的歌曲。Windows 程序。

![预览: 当前路径 → 新路径](docs/screenshot.png)

## 下载

- **可执行文件**: 从 [Releases](https://github.com/microhan1/music-folder-organizer/releases) 下载 `music-folder-organizer.exe`，双击即可，无需安装。(exe 未签名，出现 SmartScreen 警告时选择“更多信息 → 仍要运行”)
- **从源码运行** (Python 3.12):

```bash
pip install -r requirements.txt
python main.py
```

## 用法

1. 把音乐文件夹拖到窗口里。
2. 在预览表中确认 当前路径 → 新路径。不想移动的文件取消勾选。
3. 点击 **执行**。不满意可以在 **撤销…** 中查看执行记录并选择撤销(默认选中最近一次)。如果更近的执行又移动过同样的文件，会提示先撤销那一次。

点击执行之前不会移动任何文件。表格上方始终显示“移动 n 个，无变化 n 个，标签不全 n 个，重复 n 个，空文件夹 n 个”。

| 状态 | 含义 |
| --- | --- |
| 正常 | 移到新路径 |
| 无变化 | 已在正确位置 |
| 标签不全 | 缺少标题或歌手，默认不处理(可选放到“未知歌手”文件夹) |
| 重复 | 移到 `_重复` 文件夹(或回收站) |
| 冲突 | 新路径重名，加上 ` (2)` (黄色) |

## 整理规则

从预设中选择，或自己输入。点击输入框下方的占位符按钮会插入到光标位置，下面一行实时显示表中所选文件(打开文件夹前为示例歌曲)会变成什么样。

- `{album_artist|artist}/{album}/{track:02} - {title}` (默认)
- `{album_artist|artist}/{album}/{disc}-{track:02} - {title}` (仅多碟专辑为 `2-05 - 标题`)
- `{artist}/{title}`
- `{year}/{artist} - {title}`

| 占位符 | 值 |
| --- | --- |
| `{artist}` | 歌手 |
| `{album_artist}` | 专辑歌手 |
| `{album}` | 专辑 |
| `{title}` | 标题(为空时用原文件名) |
| `{track}`, `{track:02}` | 音轨号(`:02` = 两位数)。标签为空时取文件名开头的数字 |
| `{disc}` | 碟片号。仅在同一专辑有 2 张及以上碟片时出现，单碟专辑中连同旁边的分隔符一起省略 |
| `{year}` | 年份(四位) |
| `{genre}` | 流派 |
| `{artist_sort}` | 歌手排序名(用拉丁字母文件夹整理日文、中文歌手) |
| `{a\|b}` | a 为空时用 b |

空值会用“未知歌手”“未知专辑”等替代名称(可在右上角 **设置** 中修改，重复文件夹名称、artists.json·fpcalc·音乐信息填充的位置、代表名称规则也在那里)。文件名中不允许的字符(`<>:"/\|?*`)换成 `_`，路径超过 240 个字符时先缩短标题。

`.lrc`、音乐信息填充的备份(`.tagbak.json`)以及 `cover.jpg`·`folder.jpg` 会跟随音乐文件一起移动。变空的原文件夹会被删除(包括只剩 `Thumbs.db`·`desktop.ini` 的文件夹)。

**专辑附带文件**: 一个文件夹里的歌曲全部移到同一个新文件夹时，该专辑的 `.cue`·`.log`·`.txt`·`.nfo`·`.m3u`·`.pdf`·图片，以及只包含这些文件的子文件夹(`Artwork`·`Scans` 等)也会一起移动。歌曲分散到多个专辑时，或列表以外的文件(个人文档等)，会留在原处。可用选项“一并移动专辑附带文件”关闭(`--no-sidecars`)。

**带 cue 的专辑**: 文件夹里的 `.cue` 指向该文件夹的音乐文件时，不改歌曲文件名，只按整理规则移动文件夹(保证 cue 不失效，cue 内容也不改)。

## 查找重复

| 阶段 | 依据 |
| --- | --- |
| 1. 完全相同 | 文件内容相同(SHA-1) |
| 2. 同一首歌 | 歌手 + 标题相同，时长相差 2 秒以内 |
| 3. 声音 | 比较声音指纹(Chromaprint，仅本地)，标签不同也能找到同一录音 |

**只整理同一专辑内的多余副本。** 像原专辑和精选集这样收录在多张专辑中的同一首歌会显示为一组，但 **每张专辑保留一个**(组名显示“n 张专辑”)。同一专辑内保留无损(flac) → 比特率高 → 已在正确位置的文件。可用 **保留** 更改，或取消 **应用** 整组不处理。命令行中用 `--dedupe-across-albums` 可在不同专辑之间也只保留一个。查找重复页默认只显示有需要整理的组，点 **全部显示** 可看到收录在多张专辑、全部保留的组。

不保留的文件不会删除，而是移到 `_重复` 文件夹。也可以移到回收站，但移到回收站的文件无法用本程序的撤销恢复。

## 歌手合并

`岡田 有希子`、`岡田有希子`、`Yukiko Okada` 这样混用写法会分成三个文件夹。按以下顺序合并:

1. 相同的 MusicBrainz / iTunes 歌手 ID
2. 空格、全角、大小写的差异，以及末尾不同文字的括号(`Minako Yoshida (吉田美奈子)`)
3. `artists.json` — `{"岡田有希子": ["岡田 有希子", "Yukiko Okada", "오카다 유키코"]}`(键是文件夹名)
4. 同一文件夹、同一专辑里歌手写法不同时会询问“是同一歌手吗?”(蓝色)。确认后才会应用，并保存到 `artists.json`。

在 **歌手合并** 页可以更改代表名称或取消合并。

## 命令行

```bash
python main.py ./music --dry-run                       # 只输出列表，不移动
python main.py ./music --dest ./sorted --copy          # 复制到其他文件夹
python main.py ./music --dedupe --fingerprint          # 把重复(包括按声音)移到 _重复 文件夹
python main.py ./music --pattern "{artist}/{title}"
python main.py ./music --undo                          # 撤销最近一次整理
python main.py ./music --history                       # 撤销记录 (含执行 ID)
python main.py ./music --undo-run 1a2b3c4d5e6f         # 撤销选定的一次执行
```

`python main.py --help` 会用系统语言显示选项。测试: `pip install -r requirements-dev.txt` 后运行 `python samples/make_samples.py` 和 `python -m pytest tests`。

## 与音乐信息填充一起使用

标签为空时，请先用 [音乐信息填充 (music-tag-filler)](https://github.com/microhan1/music-tag-filler) 补全。

- 有标签不全的文件时，摘要栏右侧会出现 **用“音乐信息填充”打开**。点击后在那边直接打开这些文件(mp3、flac、m4a、ogg)，关闭那个窗口后这里会重新读取标签。它的位置只问一次并记住(在本程序旁边或同级的 `music-tag-filler\dist` 中会自动找到)。
- “音乐信息填充”写入的 MusicBrainz·iTunes 艺术家 ID 可把同一歌手的不同写法合并到一个文件夹。
- 它的备份(`.tagbak.json`)会随文件一起移动，整理文件夹后那边的撤销依然有效。
- **导出标签不全列表** 可把这些文件写到 `untagged.txt`。

## 不做的事

- 不修改标签，只读取(修改标签是音乐信息填充的工作)。
- 不联网。声音指纹也只在本地比较。
- 不直接删除文件，只移到 `_重复` 文件夹或回收站。
- 不做格式转换、播放、播放列表。
- 歌词、封面和专辑附带文件只跟随音乐一起移动，不修改其内容。

## 许可证

- MIT License ([LICENSE](LICENSE))
- 附带的 `third_party/fpcalc.exe`(Chromaprint，含部分 FFmpeg)为 LGPL 2.1 ([third_party/LICENSE-chromaprint](third_party/LICENSE-chromaprint))。

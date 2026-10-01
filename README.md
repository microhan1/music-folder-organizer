# 음악 폴더 정리 (Music Folder Organizer)

<img src="assets/icon.png" width="96" alt="icon">

[English](README.en.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md)

> **인터넷 연결 없이 동작합니다.**
> **기본값은 복사가 아니라 이동입니다. 되돌리기가 있습니다.**

`다운로드/새 폴더/새 폴더(2)`에 뒤섞인 음악 파일을 태그(가수·앨범·트랙·제목) 기준으로 `가수/앨범/01 - 제목.mp3` 구조로 옮기고, 같은 곡이 여러 개 있으면 찾아 정리하는 Windows 프로그램입니다.

![미리보기 화면: 현재 경로 → 새 경로](docs/screenshot.png)

## 다운로드

- **실행 파일**: [Releases](https://github.com/microhan1/music-folder-organizer/releases)에서 `music-folder-organizer.exe`를 받아 더블클릭. 설치가 필요 없습니다. (서명되지 않은 exe라 SmartScreen 경고가 뜨면 "추가 정보 → 실행")
- **소스 실행** (Python 3.12):

```bash
pip install -r requirements.txt
python main.py
```

## 사용법

1. 음악 폴더를 창에 끌어다 놓습니다.
2. 미리보기 표에서 현재 경로 → 새 경로를 확인합니다. 옮기지 않을 파일은 체크를 풉니다.
3. **실행**을 누릅니다. 마음에 안 들면 **되돌리기**로 원래 자리로 돌아갑니다.

실행 전에는 아무것도 옮기지 않습니다. 표 위에 "이동 n개, 변경 없음 n개, 태그 부족 n개, 중복 n개, 빈 폴더 n개"가 늘 보입니다.

| 상태 | 뜻 |
| --- | --- |
| 정상 | 새 경로로 옮김 |
| 변경 없음 | 이미 제자리 |
| 태그 부족 | 제목이나 가수가 비어 있음. 기본으로 제외(옵션으로 "알 수 없는 가수" 폴더에 넣기) |
| 중복 | `_중복` 폴더로 보냄(또는 휴지통) |
| 충돌 | 새 경로가 겹쳐서 ` (2)`를 붙임 (노란색) |

## 정리 규칙

프리셋 세 가지 중에서 고르거나 직접 입력합니다.

- `{album_artist|artist}/{album}/{track:02} - {title}` (기본)
- `{artist}/{title}`
- `{year}/{artist} - {title}`

| 치환자 | 값 |
| --- | --- |
| `{artist}` | 가수 |
| `{album_artist}` | 앨범 가수 |
| `{album}` | 앨범 |
| `{title}` | 제목 (비면 원래 파일명) |
| `{track}`, `{track:02}` | 트랙 번호 (`:02` = 두 자리). 태그가 비면 파일명 앞 숫자 |
| `{disc}` | 디스크 번호 |
| `{year}` | 연도 (네 자리) |
| `{genre}` | 장르 |
| `{artist_sort}` | 정렬용 가수 이름 (일본어·중국어 가수를 로마자 폴더로) |
| `{a\|b}` | a가 비면 b |

비어 있는 값은 "알 수 없는 가수", "알 수 없는 앨범" 같은 대체 문구로 채웁니다(`settings.json`의 `fallbacks`로 변경). 파일명에 쓸 수 없는 문자(`<>:"/\|?*`)는 `_`로 바꾸고, 경로가 240자를 넘으면 제목부터 줄입니다.

`.lrc`, 음악 정보 채우기의 백업(`.tagbak.json`), `cover.jpg`·`folder.jpg`는 음악 파일을 따라 함께 옮깁니다. 비게 된 원래 폴더는 지웁니다(`Thumbs.db`·`desktop.ini`만 남은 폴더 포함).

## 중복 찾기

| 단계 | 기준 | 미리보기 반영 |
| --- | --- | --- |
| 1. 완전 동일 | 파일 내용이 같음 (SHA-1) | 자동 |
| 2. 같은 곡 | 가수 + 제목이 같고 길이 차이 2초 이내 | "적용"에 체크해야 |
| 3. 소리 | 음향 지문 비교 (Chromaprint, 로컬에서만). 태그가 달라도 같은 녹음이면 묶음 | "적용"에 체크해야 |

그룹마다 남길 파일을 추천합니다: 무손실(flac) → 비트레이트 높은 쪽 → 이미 제자리에 있는 쪽. **남김** 칸을 눌러 바꿀 수 있습니다. 남기지 않는 쪽은 삭제하지 않고 `_중복` 폴더로 옮깁니다. 휴지통으로 보낼 수도 있지만, 휴지통으로 간 파일은 이 프로그램의 되돌리기로 돌아오지 않습니다.

2단계를 자동으로 적용하지 않는 이유: 베스트 앨범에 실린 곡이 원래 앨범의 곡과 같은 곡으로 묶여, 그대로 두면 앨범에서 곡이 빠져나갑니다.

## 가수 통합

`岡田 有希子`, `岡田有希子`, `Yukiko Okada`처럼 표기가 섞이면 폴더가 갈라집니다. 이렇게 묶습니다.

1. 같은 MusicBrainz / iTunes 아티스트 ID
2. 띄어쓰기·전각·대소문자 차이, 끝에 붙은 다른 문자 체계의 괄호 (`Minako Yoshida (吉田美奈子)`)
3. `artists.json` — `{"岡田有希子": ["岡田 有希子", "Yukiko Okada", "오카다 유키코"]}` (키가 폴더 이름)
4. 같은 폴더·같은 앨범인데 가수 표기가 다르면 "같은 가수인가요?"라고 묻습니다(파란색). 확인해야 적용되고, 확인하면 `artists.json`에 저장됩니다.

**가수 통합** 탭에서 대표 이름을 바꾸거나 그룹을 풀 수 있습니다.

## 명령줄

```bash
python main.py ./music --dry-run                       # 표만 출력, 아무것도 옮기지 않음
python main.py ./music --dest ./sorted --copy          # 다른 폴더로 복사
python main.py ./music --dedupe --fingerprint          # 중복(소리 포함)을 _중복 폴더로
python main.py ./music --pattern "{artist}/{title}"
python main.py ./music --undo                          # 가장 최근 정리 되돌리기
```

`python main.py --help`가 OS 언어로 옵션을 보여 줍니다. 시험: `pip install -r requirements-dev.txt` 후 `python samples/make_samples.py`, `python -m pytest tests`.

## 음악 정보 채우기와 함께

태그가 비어 있으면 [음악 정보 채우기 (music-tag-filler)](https://github.com/microhan1/music-tag-filler)로 먼저 채우세요. 태그 부족 파일은 **태그 부족 목록 내보내기**로 `untagged.txt`에 뽑을 수 있습니다. 두 프로그램은 `artists.json`을 같이 쓸 수 있습니다(`settings.json`의 `artists_path`).

## 하지 않는 것

- 태그를 수정하지 않습니다. 읽기만 합니다(태그 수정은 음악 정보 채우기의 일).
- 인터넷에 연결하지 않습니다. 음향 지문도 로컬에서만 비교합니다.
- 파일을 바로 삭제하지 않습니다. `_중복` 폴더로 옮기거나 휴지통으로 보냅니다.
- 포맷 변환·재생·재생목록 생성은 하지 않습니다.
- 가사·표지 파일은 같은 폴더의 음악 파일을 따라 옮기는 것 외에는 다루지 않습니다. `.cue`·`.log`·`Artwork` 폴더는 그 자리에 남습니다.

## 라이선스

- MIT License ([LICENSE](LICENSE))
- 동봉한 `third_party/fpcalc.exe`(Chromaprint, FFmpeg 일부 포함)는 LGPL 2.1입니다 ([third_party/LICENSE-chromaprint](third_party/LICENSE-chromaprint)).

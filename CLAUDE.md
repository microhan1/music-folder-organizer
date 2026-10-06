# music-folder-organizer 작업 규칙

PRD와 결정 사항: [docs/PRD.md](docs/PRD.md). 실제로 있었던 문제와 원인: [docs/LESSONS.md](docs/LESSONS.md).
**문제가 생기면 고친 뒤 반드시 docs/LESSONS.md에 증상·원인·해결·재발 방지를 추가한다.**

## 사용자 파일 안전

- 사용자의 실제 음악 폴더(경로는 git에 올리지 않는 `CLAUDE.local.md`)에는 절대 실행하지 않는다. 스크래치패드에 복사(.iso/.zip 제외)해서 쓰고, 실행 전후 SHA-1 스냅샷으로 비교한다.
- 작업 전후로 원본 폴더 상태(파일 수, `organize_log.json` 유무)를 확인한다. 이상하면 원인을 로그로 밝히고, 사용자 파일은 대신 되돌리지 않고 알린다.
- 실제 데이터에는 읽기 전용·숨김 파일, 태그 없는 파일, 베스트 앨범 중복이 있다고 가정한다.

## 코드 규칙

- 되돌리기 수단(로그)이 저장되기 전에는 어떤 파일도 옮기지 않는다. 처음뿐 아니라 실행 중 journal 쓰기(`RunLog.add`)도 실패하면 `LogWriteError`로 멈춘다. 로그를 읽는 곳은 모두 `mover.load_log`(journal을 합침), 저장은 `load_log`가 준 데이터로만 `save_log`(성공하면 journal을 지움). 되돌리기도 시작 전에 로그가 써지는지 확인한다. 저장 실패를 `except OSError: pass`로 삼키지 않는다(LESSONS A1·A21).
- 기존 경로의 식별은 `scan.key_of`(대소문자 무시), 새 이름 충돌은 `scan.target_key`(NFC 포함). 둘을 섞지 않는다.
- 충돌 번호가 붙는 기능을 바꾸면 재실행(멱등성) 테스트를 함께 돌린다.
- GUI worker 스레드 안에서는 위젯과 tk 변수를 건드리지 않는다.
- GUI의 다시 읽기는 `scan.TagCache`로 바뀌지 않은 파일(같은 볼륨·파일 번호·크기·수정 시각)을 열지 않는다. `Track`에 파일 내용에서 계산한 값(`sha1`·`fingerprint`처럼)을 더하면 캐시와 함께 따라오므로, 크기·수정 시각이 같으면 그대로 맞는 값만 넣는다.
- 계획(`self.plan`)은 작업 스레드에서 만든다(`_start_plan`). 넘기는 데이터는 사본으로, 화면의 계획을 실행하기 전에는 `plan_ready()`. 계획을 읽는 화면을 추가하면 계산 중에 늦게 따라와도 되는지 정한다(LESSONS A22·A23). GUI 반응성은 `mainloop` 안에서 잰다(D17).
- 한 줄에 "늘어나는 것(라벨·경로)"과 "고정 크기 것(버튼·콤보)"이 같이 있으면 고정 크기를 먼저 pack. 나중에 보였다 숨겼다 하는 위젯은 `pack(before=...)`. 줄에 위젯을 더할 때마다 그 줄 pack 순서를 다시 읽는다(LESSONS A15·A18, 세 번 재발).
- 다시 그리는 함수(`_fill_*`)가 선택·스크롤을 스스로 보존한다(A20).
- 화면에 보이는 문자열은 전부 `lang/*.json`. 키를 추가하면 4개 언어 모두 추가(`test_i18n.py`가 검사).

## 검증 순서

1. `python -m pytest tests -q` (3.14와 3.12 둘 다). GUI 테스트는 3번 연속 통과해야 함
2. GUI를 바꿨으면 스크린샷을 찍어 **이미지를 직접 열어** 확인 (테스트 통과 ≠ 화면 정상). 기본 크기와 최소 크기(1000×800), 4개 언어 모두. 폰트: UI는 언어별, 파일명 표는 맑은 고딕
3. 실제 폴더 사본으로 실행 → 되돌리기 → 스냅샷 동일 확인
4. 성능은 최악 입력(같은 태그 수천 개)으로 잰다

## 환경 함정 (Windows)

- Python 문자열에는 `C:\...` 경로. Git Bash의 `/c/...`는 조용히 실패한다.
- 출력에 한·중·일 문자가 있으면 `PYTHONIOENCODING=utf-8`.
- 백슬래시·정규식이 든 수정은 heredoc 치환 대신 Edit 도구로.
- 빌드: PowerShell에서 `build.bat`을 절대 경로로. 먼저 `Get-Process music-folder-organizer`로 사용자가 앱을 켜 뒀는지 확인하고, 켜져 있으면 끄지 말고 `--distpath build\dist-new`로 빌드.
- `dist\settings.json`은 사용자의 되돌리기 경로(`last_log`)를 담고 있으니 지우지 않는다.
- 릴리스 노트는 `docs/releases/vX.Y.Z.md`에 쓰고 커밋한 뒤 `gh release create --notes-file`로 올린다. 릴리스 빌드에서 PyInstaller를 직접 부르면 자료 경로는 절대 경로로(LESSONS D14), 끝나면 `build/`의 중간 산출물(work·spec·로그·exe 사본)을 지운다. `build/dist-settings.backup.json`과 스크린샷용 `build/demo`는 남긴다.
- 저장소에서 `python main.py`를 돌린 뒤 생긴 `settings.json`은 삭제한다.
- 테스트에서 Tk는 모듈당 하나만 만든다.

# music-folder-organizer 작업 규칙

PRD와 결정 사항: [docs/PRD.md](docs/PRD.md). 실제로 있었던 문제와 원인: [docs/LESSONS.md](docs/LESSONS.md).
**문제가 생기면 고친 뒤 반드시 docs/LESSONS.md에 증상·원인·해결·재발 방지를 추가한다.**

## 사용자 파일 안전

- 사용자의 실제 음악 폴더(경로는 git에 올리지 않는 `CLAUDE.local.md`)에는 절대 실행하지 않는다. 스크래치패드에 복사(.iso/.zip 제외)해서 쓰고, 실행 전후 SHA-1 스냅샷으로 비교한다.
- 작업 전후로 원본 폴더 상태(파일 수, `organize_log.json` 유무)를 확인한다. 이상하면 원인을 로그로 밝히고, 사용자 파일은 대신 되돌리지 않고 알린다.
- 실제 데이터에는 읽기 전용·숨김 파일, 태그 없는 파일, 베스트 앨범 중복이 있다고 가정한다.

## 코드 규칙

- 되돌리기 수단(로그)이 저장되기 전에는 어떤 파일도 옮기지 않는다.
- 기존 경로의 식별은 `scan.key_of`(대소문자 무시), 새 이름 충돌은 `scan.target_key`(NFC 포함). 둘을 섞지 않는다.
- 충돌 번호가 붙는 기능을 바꾸면 재실행(멱등성) 테스트를 함께 돌린다.
- GUI worker 스레드 안에서는 위젯과 tk 변수를 건드리지 않는다.
- 화면에 보이는 문자열은 전부 `lang/*.json`. 키를 추가하면 4개 언어 모두 추가(`test_i18n.py`가 검사).

## 검증 순서

1. `python -m pytest tests -q` (3.14와 3.12 둘 다). GUI 테스트는 3번 연속 통과해야 함
2. GUI를 바꿨으면 스크린샷을 찍어 **이미지를 직접 열어** 확인 (테스트 통과 ≠ 화면 정상)
3. 실제 폴더 사본으로 실행 → 되돌리기 → 스냅샷 동일 확인
4. 성능은 최악 입력(같은 태그 수천 개)으로 잰다

## 환경 함정 (Windows)

- Python 문자열에는 `C:\...` 경로. Git Bash의 `/c/...`는 조용히 실패한다.
- 출력에 한·중·일 문자가 있으면 `PYTHONIOENCODING=utf-8`.
- 백슬래시·정규식이 든 수정은 heredoc 치환 대신 Edit 도구로.
- 빌드: PowerShell에서 `build.bat`을 절대 경로로. 먼저 `Get-Process music-folder-organizer`로 사용자가 앱을 켜 뒀는지 확인하고, 켜져 있으면 끄지 말고 `--distpath build\dist-new`로 빌드.
- `dist\settings.json`은 사용자의 되돌리기 경로(`last_log`)를 담고 있으니 지우지 않는다.
- 저장소에서 `python main.py`를 돌린 뒤 생긴 `settings.json`은 삭제한다.
- 테스트에서 Tk는 모듈당 하나만 만든다.

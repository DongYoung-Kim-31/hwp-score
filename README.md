# hwp-score — 한글(HWP/HWPX) 문서에 악보 넣기

Claude Code 플러그인. 악보를 한글 문서에 넣을 때의 "캡처 → 자르기 → 그림판 이어붙이기 → 붙여넣기" 노가다를 대신한다.

## 할 수 있는 것

| 요청 예시 | 하는 일 |
|---|---|
| "작은 별 악보 만들어서 학습지.hwpx 2쪽에 넣어줘" | ABC/MusicXML 텍스트 악보를 verovio로 그려서 삽입 (한글 가사 지원) |
| "이 악보 PDF를 줄별로 잘라서 과제.hwpx 끝에 넣어줘" | PDF·스캔·사진에서 오선을 찾아 줄(보표) 단위로 잘라 순서대로 삽입 — 피아노 큰보표·합창·오케스트라 총보, 기울어진 스캔 지원 |
| "엘리제를 위하여 4~7마디를 한 줄로 만들어 넣어줘" | 마디줄 인식 → 여러 줄에 걸친 마디를 음자리표·조표와 함께 한 줄로 이어붙여 삽입, `--reflow 4`로 4마디씩 재배치 |

그림은 본문 여백 안에 "글자처럼 취급"으로 들어가고, 원본 문서는 덮어쓰지 않고 항상 새 파일로 저장한다.

## 설치

```bash
claude plugin marketplace add DongYoung-Kim-31/hwp-score
claude plugin install hwp-score@hwp-score
```

처음 쓸 때 Claude가 `skills/hwp-score/scripts/setup.sh`를 실행해 의존성을 설치한다(사용자 확인 후):
Python venv(`~/.cache/hwp-score/venv`: verovio, pypdfium2, numpy, pillow, scipy), Homebrew `librsvg`,
`rhwp` CLI(github.com/edwardkim/rhwp 릴리스, SHA-256 검증, `~/.local/bin/rhwp`).

요구: macOS, Python 3.10+, Homebrew. 한컴오피스는 필요 없다(결과 확인용으로만).

## Codex에서 쓰기

같은 스킬 폴더(`skills/hwp-score`)를 Codex 스킬로 그대로 쓸 수 있다.

**방법 1 — Codex에게 말로 설치** (Codex 기본 내장 `skill-installer` 사용)

```
$skill-installer install https://github.com/DongYoung-Kim-31/hwp-score/tree/main/skills/hwp-score
```

**방법 2 — 직접 복사**

```bash
git clone https://github.com/DongYoung-Kim-31/hwp-score
mkdir -p ~/.codex/skills && cp -R hwp-score/skills/hwp-score ~/.codex/skills/
```

설치 후 새 대화에서 `$hwp-score 이 악보 PDF 줄별로 잘라서 hwpx로 만들어 줘`처럼 부른다.
처음 한 번 `setup.sh`가 Python venv(`~/.cache/hwp-score`)·librsvg·rhwp를 설치하는데,
Codex 샌드박스에서는 네트워크·작업 폴더 밖 쓰기 권한 승인을 요청할 수 있다.
업데이트: `~/.codex/skills/hwp-score`를 지우고 다시 설치.

## 구성

```
.claude-plugin/plugin.json, marketplace.json
skills/hwp-score/
  SKILL.md                 Claude가 읽는 사용 지침
  scripts/setup.sh         의존성 설치
  scripts/score2png.py     ABC/MusicXML → PNG (verovio + rsvg-convert)
  scripts/insert_score.py  PNG/악보 1개를 문서에 글자처럼 삽입 (rhwp)
  scripts/split_score.py   PDF/이미지 → 줄(보표) 단위 PNG
  scripts/measures.py      마디 인식·번호 미리보기·마디 이어붙이기
  scripts/insert_split.py  줄 단위/마디 단위 결과를 순서대로 일괄 삽입
  agents/openai.yaml       Codex 표시 정보
  examples/twinkle.abc     예제 (작은 별, 한글 가사)
```

## 한계

- 일반 5선보만 인식(TAB 전용·1선 타악기 보표 제외), 3° 넘게 기울거나 원근 왜곡된 사진은 다시 스캔 필요.
- 마디 번호는 상자 순서 기준: 1·2번 괄호(볼타)나 마디 중간 도돌이표에서 인쇄 번호와 어긋날 수 있어 미리보기 이미지로 대조한다.
- 렌더링 확인은 rhwp 기준이며 한컴오피스의 쪽 나눔과 다를 수 있다.

악보 예제/테스트에 쓴 PDF는 Mutopia Project(퍼블릭 도메인).

## 라이선스

MIT

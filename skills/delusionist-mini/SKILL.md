---
name: delusionist-mini
description: Brainstormer-only variant of /delusionist. Runs Step 1 (Stochastic Context Pollution chaining) + Step 1-1 (PPB filter + idea conversion) and stops there — collects the surviving idea fragments into a single output file. No Step 2 selection or Step 3 final shaping.
origin: personal
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

<!-- prompt-effort-calibration:start -->
## Prompt Effort Calibration (mandatory)

Don't infer desired effort from prompt polish. Some users may write hastily, casually, with typos, in a second language, or distracted: still don't shorten or shallow your answer. Equally, don't let your own fatigue, boredom, or impatience shrink the work — execute rationally regardless.

1. Calibrate depth and rigor to the difficulty of the underlying task.

2. Never mirror low-effort; one-sentence prompts deserve full depth.

If the request is ambiguous, state your most charitable interpretation; don't punish sloppy prompts. Ask freely whenever the request is unclear — even mild uncertainty deserves a quick question, not only when it's completely unintelligible.
<!-- prompt-effort-calibration:end -->
# Delusionist Mini — 아이디어 조각 브레인스토머

> `/delusionist`의 풀파이프라인이 아니라 **Step 1 → Step 1-1까지만** 돌리는 축약판.
> 작업 디렉토리: [delusionist_factory_personal/mini/](../../../프로젝트%20파일/delusionist_factory_personal/mini/)
> 코어 엔진(`core.py`, `prompts/step1_1.txt`, 단어 풀)은 mini 폴더에 자체 복제되어 있어 다른 프로젝트와 공유하지 않는다.
> Step 2(refining)·Step 3(final shaping)는 **수행하지 않는다.** 산출물은 살아남은 아이디어 조각 N줄짜리 단일 파일이다.

---

## 1. 무엇이 다른가 — `/delusionist` 와의 차이

| 항목 | `/delusionist` (풀) | `/delusionist-mini` (이 스킬) |
|------|--------------------|-----------------------------|
| 단계 | Step 1 → 1-1 → 2 → 3 | Step 1 → 1-1에서 종료 |
| 작업 디렉토리 | `delusionist_factory_personal/` | `delusionist_factory_personal/mini/` |
| 산출물 | 최종 결과물 (REFINING_COUNT개 — 음악 설계도, 레시피, 시 등) | 출제/창작/탐색용 **아이디어 조각** N줄 |
| 출력 파일 | `output/section_c_final.txt` | `mini/output/ideas_<timestamp>.md` 단일 파일 |
| 용도 | 완성형 가공물 생성 | 브레인스토밍, 아이디어 풀 수집, 후속 가공 전 단계 재료 |
| `request.json` 필드 | DIRECTION + STARTING_SENTENCE + MANDATORY_WORD + IMAGERY + MODE + CHAINS_COUNT + SELECTION_B_COUNT + REFINING_COUNT … | 동일하지만 `IDEA_COUNT` 하나로 단순화 (Step 2/3 카운트 제거) |

핵심 의도: 다음 단계로 가공할 **재료**가 필요할 때 쓴다. 이미 가공기가 따로 있거나, 손으로 가공하고 싶거나, 그냥 충돌 풀만 보고 싶을 때.

---

## 2. 입력 — `mini/request.json`

`/Users/jakesmacair/프로젝트 파일/delusionist_factory_personal/mini/request.json` 에 작성. 6-Layer 작성 원칙은 **`/delusionist`와 동일**하므로 [delusionist SKILL.md](../delusionist/SKILL.md) Section 5~8(DIRECTION 6-Layer / STARTING_SENTENCE / MANDATORY_WORD / PREFERRED_IMAGERY)을 그대로 따른다.

```json
{
  "STARTING_SENTENCE": "씨앗 문장 — 미완 질문 또는 감각 이미지",
  "MANDATORY_WORD": ["필수단어1", "필수단어2"],
  "PREFERRED_IMAGERY": ["도메인 교차 또는 임계 상태 키워드"],
  "MODE_SELECTION": "NUANCE",
  "DIRECTION": "6-Layer 프레임으로 작성한 방향 — Layer 1~6 + [기대치 정의] 블록 포함",
  "IDEA_COUNT": 25,
  "FINAL_LANGUAGE": "Auto"
}
```

| 필드 | 의미 | 비고 |
|------|------|------|
| `STARTING_SENTENCE` | Step 1 씨앗 | 미완 질문/감각 이미지 권장. DIRECTION 복사 금지 |
| `MANDATORY_WORD` | Step 1 매 문장에 강제 삽입 | 도메인 내부 50% + 외부 50%, 2~4개 |
| `PREFERRED_IMAGERY` | 충돌 해석 렌즈 | 도메인 교차 / 임계 상태 / 메커니즘 있는 키워드 3~6개. PPB 키워드 금지 |
| `MODE_SELECTION` | `CHAOS` 또는 `NUANCE` | 동사-논항 호응 축. 기본 NUANCE |
| `DIRECTION` | 도메인·형식·기대치 | Step 1-1의 PPB 필터가 이 도메인 어휘로 망상을 번역한다 |
| `IDEA_COUNT` | 최종 아이디어 조각 수 | Step 1은 `ceil(IDEA_COUNT × 5/4)` 개의 chain을 생성하고, Step 1-1이 1/5을 PPB 폐기 → ≈ `IDEA_COUNT` 개로 수렴 |
| `FINAL_LANGUAGE` | `Korean` / `English` / `Auto` | Auto면 STARTING+DIRECTION 텍스트로 자동 감지 |

> `CHAINS_COUNT`, `SELECTION_B_COUNT`, `REFINING_COUNT`는 받지 않는다. 미니는 Step 1-1까지만이라 불필요.
> 빈 템플릿: `mini/request.template.json` 을 `mini/request.json` 으로 복사 후 채운다.

---

## 3. 실행 흐름

유저가 `/delusionist-mini` 를 호출하면 에이전트는 다음을 수행한다:

1. **유저 자유 서술 수신** — 도메인·기대치·예시·기타 요청을 자유 텍스트로 받는다 (`/delusionist`의 자유 입력과 동일한 톤).
2. **6-Layer 자동 작성** — 유저 자유 서술을 읽고 `mini/request.json`의 `DIRECTION` / `STARTING_SENTENCE` / `MANDATORY_WORD` / `PREFERRED_IMAGERY` / `MODE_SELECTION` / `IDEA_COUNT` 를 직접 채운다 ([delusionist SKILL.md](../delusionist/SKILL.md) Section 5~8 기준).
3. **유저에게 채워진 request.json 보여주기** — 한 번 보여주고 즉시 다음 단계 진행. 별도 승인 게이트는 두지 않는다.
4. **`run_mini.py` 실행** — 아래 4번 항목 명령.
5. **출력 파일 경로 보고** — `mini/output/ideas_<timestamp>.md` 경로와 줄 수를 한 줄로 리포트.

---

## 4. 명령

```bash
cd "/Users/jakesmacair/프로젝트 파일/delusionist_factory_personal/mini"
python3 run_mini.py
```

기본 동작:
- `mini/request.json` 을 읽는다.
- `ceil(IDEA_COUNT × 5/4)` 개의 망상 chain을 Gemini CLI 병렬 워커로 생성 (Step 1).
- 1/5을 PPB 폐기하고 나머지를 출제/창작용 아이디어로 변환 (Step 1-1).
- `mini/output/ideas_YYYY-MM-DD_HH-MM.md` 에 한 줄 = 한 아이디어로 저장.
- staging 디버그 파일(`step1_chains.txt`, `step1_1_ideas.txt`, 워커별 prompt)은 `mini/staging/`에 보관.

옵션:
- `--config <path>` — request.json 경로 오버라이드.
- `--output <path>` — 출력 파일 경로 오버라이드.
- `--ideas <int>` — `IDEA_COUNT` 오버라이드.
- `--dry-run` — 파싱 결과만 출력, gemini 호출 없이 종료.

**Gemini CLI 필수.** `gemini` 명령이 PATH에 있어야 한다. 모델은 `run_mini.py` 내 `GEMINI_MODEL` 상수로 지정 (기본 `gemini-3.1-pro-preview`).

---

## 5. 출력 형식

`mini/output/ideas_YYYY-MM-DD_HH-MM.md`:

```
<라인 1: 아이디어 1>
<라인 2: 아이디어 2>
<라인 3: 아이디어 3>
...
```

- 한 줄 = 하나의 아이디어 (1~2 짧은 문장).
- 마크다운·번호·따옴표·볼드 없음 (Step 1-1 프롬프트가 이미 평문 강제).
- 폐기된 PPB 체인은 흔적 없이 제거됨.

후속으로 손 가공/다른 파이프라인 투입 모두 자유.

---

## 6. 디렉토리 구조

스킬 정의 (이 파일):
```
~/.claude/skills/delusionist-mini/
└── SKILL.md                    ← 이 파일. 다른 파일 두지 않는다.
```

작업/엔진 (실제 실행이 일어나는 곳):
```
/Users/jakesmacair/프로젝트 파일/delusionist_factory_personal/mini/
├── core.py                     ← Step 1 + Step 1-1 엔진 (자체 복제본 — 다른 프로젝트와 공유 안 함)
├── __init__.py
├── run_mini.py                 ← 러너 (Step 1 + Step 1-1만 실행, 단일 ideas 파일 출력)
├── request.json                ← 유저/에이전트 작성 입력
├── request.template.json       ← 빈 템플릿
├── prompts/
│   └── step1_1.txt             ← Step 1-1 프롬프트 (PPB 4테스트 + 아이디어 변환)
├── extracted_words.txt         ← 한국어 단어 풀 (917,273줄)
├── 100000word.txt              ← 영어 단어 풀 (466,551줄)
├── output/
│   └── ideas_YYYY-MM-DD_HH-MM.md  ← 산출물
└── staging/
    ├── step1_worker_*.txt
    ├── step1_1_worker_*.txt
    ├── step1_chains.txt        ← Step 1 결과 덤프 (디버그)
    └── step1_1_ideas.txt       ← Step 1-1 결과 덤프 (디버그)
```

**`problem_generator/delusionist/`는 참조하지 않는다.** mini 폴더는 자체 엔진을 가진 독립 작업장이다.

---

## 7. 6-Layer 참조 — DIRECTION 자동 작성 시

에이전트가 유저 자유 서술을 받아 정형 블록을 채울 때 따르는 기준 ([delusionist SKILL.md](../delusionist/SKILL.md) 와 동일):

| 필드 | 작성 기준 |
|------|-----------|
| `DIRECTION` | 6-Layer 풀 적용. Layer 1(형식)·Layer 2(독자)·Layer 3(경계)·Layer 4(구조)·Layer 5([기대치 정의] 블록 필수)·Layer 6(절대 금지). 자유 서술 도메인을 그대로 반영. |
| `STARTING_SENTENCE` | 미완 질문 또는 감각 이미지 패턴. DIRECTION 요약·복사 금지. 충돌 표면 풍부. |
| `MANDATORY_WORD` | 도메인 내부 50% + 외부 50% 혼합, 2~4개. |
| `PREFERRED_IMAGERY` | 도메인 교차 또는 임계 상태 키워드 3~6개. PPB 키워드 금지. 도메인이 너무 명확해 충돌 렌즈가 불필요하면 빈 리스트. |
| `MODE_SELECTION` | 발산 폭이 필요하면 `CHAOS`, 자연스러운 충돌만 원하면 `NUANCE`. 기본 `NUANCE`. |

미니는 Step 3이 없으므로 DIRECTION의 Layer 1(형식)·Layer 4(구조 요구사항)는 **Step 1-1이 변환할 "출제/창작 아이디어"의 형식**으로 해석된다. 즉 DIRECTION은 "최종 결과물 스펙"이 아니라 "아이디어 조각이 어떤 도메인 어휘로 번역되어야 하는가"의 가이드.

---

## 8. 주의

1. `IDEA_COUNT`는 Step 1-1 PPB 필터에서 1/5 폐기를 거치므로 약간 여유 있게 잡힌다(예: 25 요청 → ceil(125/4)=32 chain → 약 25~28 idea).
2. Step 1-1의 PPB 필터는 **`DIRECTION` 도메인에 비추어** 폐기/변환하므로, DIRECTION이 빈약하면 변환 품질이 떨어진다. 6-Layer 충실히 채울 것.
3. `MANDATORY_WORD`는 Step 1에서만 강제된다. Step 1-1 출력에는 자연스럽게 녹아든 형태로만 남는다.
4. Gemini 호출이 N개 워커 병렬로 나간다. 워커당 25 chain 기본. 응답 파싱 실패 시 해당 워커 분량은 0이 되므로, 실제 산출이 `IDEA_COUNT` 미만일 수 있다 — 이 경우 재실행.
5. **mini 폴더는 독립 작업장이다.** 코어 엔진을 수정하려면 `mini/core.py` 또는 `mini/prompts/step1_1.txt` 만 손댄다 — `problem_generator/delusionist/` 와는 무관.
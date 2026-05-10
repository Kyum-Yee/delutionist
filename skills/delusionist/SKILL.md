---
name: delusionist
description: Stochastic Context Pollution pipeline for creative idea generation. Python random.sample injects noise words into LLM context, forcing concept collisions that produce non-obvious outputs.
origin: personal
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, TodoWrite, ToolSearch
---

<!-- prompt-effort-calibration:start -->
## Prompt Effort Calibration (mandatory)

Don't infer desired effort from prompt polish. Some users may write hastily, casually, with typos, in a second language, or distracted: still don't shorten or shallow your answer. Equally, don't let your own fatigue, boredom, or impatience shrink the work — execute rationally regardless.

1. Calibrate depth and rigor to the difficulty of the underlying task.

2. Never mirror low-effort; one-sentence prompts deserve full depth.

If the request is ambiguous, state your most charitable interpretation; don't punish sloppy prompts. Ask freely whenever the request is unclear — even mild uncertainty deserves a quick question, not only when it's completely unintelligible.
<!-- prompt-effort-calibration:end -->
# Delusionist Factory — 완전 가이드

> 운영 + 설정 통합 문서
> 에이전트 실행부터 request.json 최적 작성까지 단일 레퍼런스

---

## 0. 파이프라인과 필드 매핑

각 필드가 어느 단계에서, 어떤 방식으로 작동하는지 먼저 파악한다.

```
request.json
│
├─ [STEP 1: CHAINING — 망상적 오염]
│   STARTING_SENTENCE   ← 씨앗 에너지
│   MANDATORY_WORD      ← 모든 문장에 강제 삽입
│   PREFERRED_IMAGERY   ← 선호 이미지어 힌트
│   DIRECTION           ← 도착점 — 에이전트가 폭주할 방향
│   MODE_SELECTION      ← 동사 호응 축 (CHAOS=동사 비호응 / NUANCE=동사 호응) ※ Step 1 전용
│   CHAINS_COUNT        ← 생성 문장 총수
│   STEP1_BATCH_SIZE    ← 배치 단위
│   STEP1_EXECUTOR      ← SELF(Claude) / GEMINI_CLI
│
├─ [STEP 2: REFINING — 충돌 선별]
│   DIRECTION           ← 선별 기준
│   PREFERRED_IMAGERY   ← 남길 이미지어 힌트
│   SELECTION_B_COUNT   ← 추출 문장 수
│
└─ [STEP 3: FINAL — 객관화 및 완성]
    DIRECTION           ← 최종 결과물 스펙 (여기서 가장 중요)
    FINAL_LANGUAGE      ← 출력 언어
    LANGUAGE_RULE       ← 언어 혼용 규칙
    REFINING_COUNT      ← 최종 결과물 개수
```

**핵심 통찰:** `DIRECTION`은 세 단계 모두에 주입되지만 역할이 다르다.
- Step 1: "이 방향으로 폭주하라"는 나침반
- Step 2: "이 기준에 맞는 것만 남겨라"는 필터
- Step 3: "이 형식으로 완성하라"는 설계도

---

## 1. 프로젝트 개요

LLM은 혼자서는 자기 자신을 놀라게 할 수 없다. 같은 맥락을 주면 확률적 평균으로 수렴해서 진부한 결과가 나온다.

Delusionist Factory는 **Python의 random.sample로 무작위 단어를 뽑아서 LLM의 맥락을 강제로 오염시킨다.** 오염된 맥락에서 AI는 평소라면 절대 연결하지 않았을 개념들을 충돌시키게 되고, 그 충돌에서 창의적인 아이디어가 나온다.

이것을 **Stochastic Context Pollution (확률적 맥락 오염)** 이라 부른다.

---

## 2. 3단계 파이프라인

전체 흐름은 **Step 1 → Step 2 → Step 3** 순서로 진행된다.

### Step 1: Chaining (A단계) - 원석 채굴
- **목표**: CHAINS_COUNT개의 "망상적 변이 문장" 대량 생산
- **메커니즘**: Python이 단어 풀에서 무작위 3단어를 뽑아줌 → AI가 그 단어들과 STARTING_SENTENCE를 충돌시켜 문장 생성
- **출력 파일**: `output/section_a_chains.txt` (한 줄에 한 문장)
- **배치 단위**: 100문장씩
- **출력 포맷**: 넘버링 + 무작위 단어 **볼드** 표시 + 문장 끝 괄호 주석
  - 예: `1. 발효가 **균열**을 타고 **산화**되는 순간, 시간은 냄새가 된다. (시간-냄새 교차: C단계에서 발효 개념과 연결 가능)`
- **핵심 규칙**:
  - MANDATORY_WORD는 매 문장에 **반드시** 전부 포함
  - 한국어+영어 혼용 시 영어 3단어 연속 사용 금지 (LANGUAGE_RULE)
  - 무작위 단어가 뜬금없으면 그대로 쓰지 말고 문맥에 맞게 치환
  - 과감할 것. 안전하고 말이 되는 문장은 실패
- **실행 방식**: STEP1_EXECUTOR 값에 따라 다름
  - `"SELF"`: Agent가 직접 문장 생성
  - `"GEMINI_CLI"`: main.py가 Gemini CLI용 프롬프트를 `staging/step1_gemini_prompt.txt`에 생성 → 외부에서 Gemini CLI로 실행 → 결과를 section_a_chains.txt에 append

### Step 2: Refining (B단계) - 선별과 명명
- **목표**: Step 1의 대량 문장에서 SELECTION_B_COUNT개의 정제된 아이디어 추출
- **출력 파일**: `output/section_b_refined.txt`
- **출력 포맷**: 각 문장 끝에 괄호 주석 필수 — 인상적인 점, C단계 활용 방향 메모
- **배치 단위**: 10문장씩
- **핵심 작업**:
  1. Step 1에서 "기존에 함께 쓰이지 않던 관념/아이디어의 조합"을 찾기
  2. 찾아낸 충돌에 **고유한 이름** 붙이기 (기존 용어 재사용 금지)
  3. 그 이름이 가리키는 실체를 구체적으로 서술
  4. 기존의 단일 관념으로 환원 가능하면 탈락
  5. 고유명사 나열, 비유의 비유, 미사여구 3단 중첩 → 즉시 삭제

### Step 3: Final (C단계) - 완성형 가공
- **목표**: Step 2의 재료를 REFINING_COUNT개의 최종 결과물로 가공
- **출력 파일**: `output/section_c_final.txt`
- **배치 단위**: 5개씩
- **핵심 작업**:
  1. DIRECTION에 맞는 형식과 구조로 최종 결과물 구성
  2. DIRECTION 작성자의 어휘 수준/톤에 맞춤 (독해 난이도 조절)
  3. 자기 표현 제한: 제목 1개 + 핵심 키워드 1개까지만 독창적 표현 허용
  4. 나머지는 DIRECTION과 Step 2 재료에서 유추 가능한 표현으로 구성
  5. 주관적 감탄/미사여구 배제, "~하면 ~가 된다" 조건-결과 구조로 서술
  6. 읽으면 바로 활용 가능한 형태 (해석이 필요한 암호가 아닐 것)
- **C단계 작성 기준** (작성하는 동안 적용하는 판단 기준):
  - **완결성**: 전제/비유/질문/비교를 열 때, 귀결시킬 수 있는 경우에만 연다. 귀결 계획 없이 여는 것 금지.
  - **추적 가능성**: A→B 전환 시, 독자가 근거를 본문 내에서 재구성할 수 있어야 한다. 근거 없는 전환은 만들지 않는다.
  - **자족성**: 메타 설명("이 글은~", "여기서 말하고자 하는 것은~") 쓰지 않는다. 메타가 필요하면 본문 자체가 불충분하다는 신호.

---

## 3. 실행 방법 — MCP 도구 호출 시나리오

이 스킬은 **원격 MCP 서버를 호출하는 클라이언트(에이전트) 관점**의 가이드다. endpoint는 `https://kyumyee-playground.onrender.com/delusionist/mcp/`. 모든 작업은 도구 호출로 완성된다 — 파일 시스템 직접 접근은 필요 없다.

### 3.1 도구 카탈로그 (역할별)

| 카테고리 | 도구 | 역할 | agent_id |
|---|---|---|---|
| **세션 라이프사이클** | `register_agent` | 첫 호출. 임의 `agent_id` 발급 + 격리 workspace 생성. 24h 슬라이딩 TTL | — |
| | `release_agent` | 작업 종료 후 즉시 정리 (선택; TTL이 자동 정리) | ✓ |
| | `list_agents` | 등록된 agent 목록 (운영·디버깅) | — |
| **참조 문서** | `get_request_guide` | 가이드 전문 반환 | — |
| | `get_skill_delutionist` | 이 스킬을 JSON으로 받아 `~/.claude/skills/delusionist/`에 저장 가능 | — |
| | `get_skill_delutionist_mini` | mini 변종 스킬 반환 | — |
| **설정 (request.json)** | `update_request_config` | 그 agent의 request.json을 merge 갱신. 첫 호출 시 사실상 생성 | ✓ |
| | `get_request_config` | 현재 설정 조회 | ✓ |
| **파이프라인 진행** | `run_delusionist` | **핵심 실행 도구.** 현재 step 확인 → 다음 step의 작업 지시(SELF) 또는 외부 cmd(GEMINI_CLI) 반환. 반복 호출이 정석 | ✓ |
| | `get_status` | step별 진행 카운트 + finalized 플래그 | ✓ |
| | `append_result` | 생성한 문장을 해당 step에 append. `finalize=true`는 Step 3 완료 신호 | ✓ |
| | `read_output_file` | 누적된 step별 출력 텍스트 조회 | ✓ |
| | `reset_factory` | 그 agent의 output/staging 초기화 (request.json은 보존). `confirm=true` 필수 | ✓ |
| **Step 1 보조** | `prepare_parallel_gemini_workers` | Step 1을 N개 Gemini CLI 워커로 분할. cmd 배열 반환 | ✓ |
| | `get_random_words` | 단어 풀에서 무작위 N개 직접 추출 (디버깅·실험용) | ✓ |
| **Mini (Step 1 + 1-1)** | `prepare_mini_step1_workers` | mini의 Step 1 워커 분할 (`executor=GEMINI` 또는 `SELF`) | ✓ |
| | `prepare_mini_step1_1_workers` | mini의 Step 1-1 (PPB ~1/5 폐기 + 아이디어 변환) 워커 분할 | ✓ |
| | `parse_mini_step1_response` | Gemini 응답 → chain 줄 배열. stateless | — |
| | `parse_mini_step1_1_response` | Gemini 응답 → idea 줄 배열. stateless | — |

### 3.2 표준 워크플로우 (3-step pipeline, 한 사이클)

```text
0. register_agent
   → response.agent_id 보관 (예: "a-3f2k7d9c"). 이후 모든 작업 도구 호출에 인자로 전달.

1. update_request_config(agent_id, config={
       STARTING_SENTENCE, MANDATORY_WORD, PREFERRED_IMAGERY,
       CHAINS_COUNT, SELECTION_B_COUNT, REFINING_COUNT,
       MODE_SELECTION, STEP1_EXECUTOR,
       DIRECTION, FINAL_LANGUAGE, ...
   })
   ← §4~§9에 따라 작성

2. while True:
       result = run_delusionist(agent_id)
       if "ALL STEPS COMPLETE" in result: break

       # ─ Step 1 (Chaining) ─
       #   STEP1_EXECUTOR="GEMINI_CLI" (기본):
       #     result에 prompt path + gemini cmd 포함. Bash로 실행 후 결과를
       #     append_result(agent_id, step="1", content=...)로 올린다.
       #     또는 prepare_parallel_gemini_workers(agent_id, batch_size=25)로
       #     N개 병렬 워커 받아서 cmd N개 동시 실행.
       #   STEP1_EXECUTOR="SELF":
       #     result에 random words 배정표 + 작업 지시. agent가 직접 N개
       #     문장 생성 → append_result(step="1", content=...).
       #
       # ─ Step 2 (Refining) ─
       #   read_output_file(agent_id, step="1")로 chains 읽고, v3 룰
       #   (깊이 보존 / 추상성 제거, PRUNING, 명명 규칙) 적용해 정제 →
       #   append_result(step="2", content=...).
       #
       # ─ Step 3 (Final) ─
       #   read_output_file(agent_id, step="2")로 refined 읽고, Main Idea
       #   선정 → 최종 결과물 작성 → append_result(step="3", content=...).
       #   마지막 호출에 finalize=true.

3. (선택) release_agent(agent_id)
```

### 3.3 Mini 워크플로우 (Step 1 + 1-1만, 가벼운 발산)

mini는 "Step 2/3 없이 발산만". PPB ~1/5 폐기 후 한 줄당 한 아이디어로 변환된 결과를 클라이언트가 받아간다. mini는 **request.json 사용 안 함** — 워커 분할 도구의 인자로 모든 설정을 직접 전달한다.

```text
0. register_agent → agent_id

1. resp1 = prepare_mini_step1_workers(
     agent_id,
     chains_count=N,           # 생성할 총 chain 수 (목표 idea × 1.25 권장)
     chains_per_worker=25,
     direction="...",          # DIRECTION (필수, 6-Layer)
     starting="...",
     mandatory=["..."],
     imagery=["..."],
     mode="NUANCE"|"CHAOS",
     final_language="Korean"|"English"|"Auto",
     executor="GEMINI"|"SELF"
   )
   → resp1.workers[*].prompt (executor=SELF) 또는 .cmd (executor=GEMINI)

2. for w in resp1.workers:
       executor=GEMINI: stdout = bash(w.cmd)
       executor=SELF:   stdout = agent가 w.prompt 따라 w.line_count 줄 생성

3. chains = []
   for raw in stdouts: chains.extend(parse_mini_step1_response(raw))

4. resp2 = prepare_mini_step1_1_workers(
     agent_id, chains=chains,
     direction="...", mandatory=["..."],
     final_language=..., executor="GEMINI"|"SELF"
   )
   → 같은 패턴으로 워커 실행

5. ideas = []
   for raw in stdouts: ideas.extend(parse_mini_step1_1_response(raw))

6. # 클라이언트가 한 줄당 한 아이디어 형식으로 직접 저장
7. (선택) release_agent(agent_id)
```

### 3.4 에러 패턴

| 응답 | 의미 | 대응 |
|---|---|---|
| `ERROR: 'agent_id' is required.` | agent_id 누락 | 첫 호출은 `register_agent` |
| `ERROR: agent_id 'a-...' is unknown or expired.` | 모르는·만료된 ID. 만료 시 workspace 자동 삭제 | 새로 `register_agent` |
| `ERROR: request.json not found.` | 첫 `update_request_config` 안 함 | `update_request_config(agent_id, config={...})` |
| `STEP 1 already complete.` | 줄 수가 이미 채워짐 | `run_delusionist`로 Step 2 자동 진입 |
| `STEP 1 is external (Gemini CLI)...` | 정상 흐름. result에 prompt path + cmd 포함 | Bash로 cmd 실행 → 결과를 `append_result` |

### 3.5 격리 + TTL 약속

- agent_id별로 4개 디렉토리(`output / staging / input / mini-staging`)가 서버 측에 격리되어 있다. 클라이언트는 이 경로를 직접 만질 필요가 없다.
- `request.json`도 agent별로 분리. 한 agent의 `update_request_config` 호출은 다른 agent에게 영향 없음.
- 매 도구 호출이 그 agent의 `expires_at`을 *호출 시각 + 24h*로 갱신한다 (sliding window).
- TTL이 만료되면 그 agent의 workspace가 자동 삭제된다. 다음 호출이 거부되며, 새로 `register_agent` 필요.

---

## 4. 필드 레퍼런스

파일 위치: `input/request.json`

```json
{
  "STARTING_SENTENCE": "시작 문장 또는 주제",
  "MANDATORY_WORD": ["필수단어1", "필수단어2"],
  "PREFERRED_IMAGERY": ["이미지어1", "이미지어2"],
  "CHAINS_COUNT": 200,
  "MODE_SELECTION": "NUANCE",
  "SELECTION_B_COUNT": 10,
  "REFINING_COUNT": 5,
  "STEP1_EXECUTOR": "SELF",
  "DIRECTION": "최종 결과물의 방향성 (Section 5 참조)",
  "FINAL_LANGUAGE": "Korean",
  "LANGUAGE_RULE": "NO_3_CONSECUTIVE_FOREIGN_WORDS"
}
```

| 필드 | 설명 | 기본값 |
|------|------|--------|
| STARTING_SENTENCE | Step 1의 시작점. 주제/분위기/에너지의 씨앗 | (필수) |
| MANDATORY_WORD | 모든 문장에 반드시 포함할 단어 배열 | [] |
| PREFERRED_IMAGERY | 개념 방향벡터 — 무작위 단어 충돌의 해석 렌즈 | [] |
| CHAINS_COUNT | Step 1에서 생성할 총 문장 수 | 100 |
| MODE_SELECTION | `"CHAOS"` 또는 `"NUANCE"` — Step 1 문장 생성 시 동사 의미 호응 여부를 결정한다 (Step 2·3에는 영향 없음). 정의는 Section 4-1 참조. | "NUANCE" |
| SELECTION_B_COUNT | Step 2에서 추출할 정제 문장 수 | 10 |
| REFINING_COUNT | Step 3 최종 출력 수 | 2 |
| STEP1_EXECUTOR | `"SELF"` (Agent 직접) 또는 `"GEMINI_CLI"` (외부 Gemini) | "GEMINI_CLI" |
| DIRECTION | 최종 결과물의 방향성 (Section 5 참조) | (필수) |
| FINAL_LANGUAGE | 최종 출력 언어. "Korean" 또는 "English" | "Korean" |
| LANGUAGE_RULE | 언어 혼용 규칙 | "NO_3_CONSECUTIVE_FOREIGN_WORDS" |

### 필드 중요도 Tier

#### Tier 1 — 결과 품질을 결정하는 필드

| 필드 | 영향 범위 | 잘못 쓰면 |
|------|-----------|-----------|
| `DIRECTION` | Step 1~3 전체 | 뻔한 결과 / 프레임 이탈 |
| `STARTING_SENTENCE` | Step 1 씨앗 에너지 | 출발이 진부하면 체이닝 전체가 평균수렴 |
| `MANDATORY_WORD` | Step 1~2 강제 삽입 | 도메인만 채우면 연결이 뻔해지고, 외부만 채우면 맥락 붕괴 |

#### Tier 2 — 발산의 폭과 질을 조정하는 필드

| 필드 | 효과 | 기본값 |
|------|------|--------|
| `MODE_SELECTION` | CHAOS: 동사-논항 호응 제약 해제 — 비호응 허용, 호응도 허용 (Step 1 한정) / NUANCE: 동사-논항 호응 강제, 비호응이 있다면 동사 외 성분들 간에서만 — 전부 호응해도 통과 (Step 1 한정) | NUANCE |
| `PREFERRED_IMAGERY` | 개념 방향벡터 — 무작위 단어 충돌의 해석 렌즈 | 없음 (직접 지정 권장) |
| `CHAINS_COUNT` | 발산 풀 크기 — 많을수록 희귀 연결 확률 상승 | 100~200 |

#### Tier 3 — 기술적 파라미터

| 필드 | 설명 |
|------|------|
| `SELECTION_B_COUNT` | Step 1에서 추출할 문장 수. `CHAINS_COUNT`의 20~30% 권장 |
| `REFINING_COUNT` | 최종 결과물 개수. 보통 1~5 |
| `STEP1_BATCH_SIZE` | 한 번에 생성할 문장 수. 기본 25 |
| `STEP1_EXECUTOR` | `SELF`(Claude 직접) / `GEMINI_CLI`(외부 Gemini CLI) |
| `FINAL_LANGUAGE` | `Korean` / `English` — 어휘 풀도 자동 전환됨 |
| `LANGUAGE_RULE` | 기본 `NO_3_CONSECUTIVE_FOREIGN_WORDS` |

---

## 4-1. MODE_SELECTION — 동사-논항 호응 축

`MODE_SELECTION`은 **Step 1(Chaining)에서 생성되는 각 문장에서, 동사와 그 논항(주어·목적어)이 의미적으로 호응할지 여부**를 결정한다. 즉 술어 차원의 망상을 허용할지(CHAOS), 술어 바깥으로 망상을 한정할지(NUANCE)를 가른다. Step 2(Refining)와 Step 3(Final)에는 영향을 주지 않는다 — 그 단계는 Step 1에서 만들어진 재료만 다룬다.

### CHAOS — 동사-논항 호응 제약 해제

문장의 동사가 자기 논항(주어·목적어)과 의미적으로 호응할 의무가 없다. 비호응이 허용되며, 호응도 동등하게 허용된다 — 어느 쪽도 강제되지 않는다. 동사 외의 문장 성분들에 대해서도 추가 제약은 없다. 술어 차원의 망상이 가능해지는 모드다.

### NUANCE — 동사-논항 호응 강제

문장의 동사는 자기 논항(주어·목적어)과 의미적으로 호응해야 한다. 동사가 가리키는 행위는 그 주체·대상에 적용 가능해야 한다. 동사-논항 결합에서의 비호응은 금지. 비호응이 발생한다면 **동사 외의 문장 성분들 간**(수식어, 배경 설정, 함께 등장하는 요소들)에서만 허용된다. 모든 성분이 완전히 호응하는 문장도 통과한다 — 호응 상태에서 좋은 아이디어가 나오는 경우를 배제하지 않는다. 실패는 오직 동사가 자기 논항에 결합 불가능한 형태일 때다.

### 적용 범위

- **적용**: Step 1 프롬프트(Gemini CLI 워커, SELF 모드 모두). 매 문장에 강제된다.
- **미적용**: Step 2 선별, Step 3 최종 가공. MODE 정의는 두 단계의 판단 기준에 들어가지 않는다.

---

## 5. DIRECTION — 6-Layer 작성 프레임워크

`DIRECTION`은 단순한 지시문이 아니라 **AI가 3개 단계를 거쳐 도달해야 할 정밀한 설계도**다. 6-Layer 프레임워크를 순서대로 적용한다.

> **원칙:** Step 3의 최종 결과물이 "DIRECTION을 쓴 사람의 언어로 쓰인 것"처럼 느껴져야 한다. 즉, DIRECTION의 어투·전문성·구조가 Step 3의 결과물로 직접 역투영된다.

---

### Layer 1 — Task: 형식과 결과물 스펙

**쓸 것:** 최종 결과물의 포맷, 섹션 구성, 각 항목이 반드시 포함해야 할 내용.
**쓰지 말 것:** 분위기, 감성, 이미지어 (그것은 `PREFERRED_IMAGERY`와 Step 1~2가 담당).

```
// 좋은 예: 형식이 명확하다
"3곡의 작곡 설계도. 각 곡당 [제목/핵심감정] [조성/BPM/박자]
[전체 구조: 섹션별 명칭·길이·다이내믹] [악기 레이어 전체 명세] 포함."

// 나쁜 예: 형식이 없고 분위기만 있다
"뼛속까지 내려오는 감동을 주는 뉴에이지 음악에 관한 글."
```

**체크리스트:**
- [ ] 결과물의 포맷이 명시됐는가? (시 / 에세이 / 설계도 / 가이드 / 시트 등)
- [ ] 결과물에 반드시 포함될 항목을 열거했는가?
- [ ] "읽으면 즉시 행동 가능한 수준"인지 기준이 있는가?

---

### Layer 2 — Tone: 독자와 어투

**쓸 것:** 이 결과물을 누가 읽는가, 그 독자의 전문성 수준, 요구되는 어투.
**쓰지 말 것:** AI의 퍼소나 (여기서는 결과물의 독자 기준이지, AI 역할 지시가 아님).

```
// 예시
"현악 편곡 경험이 있는 작곡가가 읽는 수준. 음악 이론 용어 직접 사용.
  약식 설명 없이 전문 어휘 그대로."

"중학생도 이해할 수 있는 수준. 전문 용어 등장 시 즉시 풀이."
```

**체크리스트:**
- [ ] 독자의 전문성 수준이 명시됐는가?
- [ ] 어투(서술형 / 지시형 / 에세이형)가 지정됐는가?

---

### Layer 3 — Background: 맥락 펜스

**쓸 것:** 이 결과물이 절대 넘어가면 안 되는 영역 경계, 도메인 특유의 금기사항.
이 레이어를 생략하면 Step 3 에이전트가 "그럴 듯한 것"으로 프레임을 벗어난다.

```
// 예시
"가사 없음. 무도회 감성이되 단조로운 클래식 반복 금지.
  인스타 brunch 브금과 완전히 다른 결—경박한 설렘이 아니라 뼛속까지 내려오는 감동."

"철학 에세이 형식이되, 학술 논문 구조(서론-본론-결론-참고문헌)는 금지.
  독자가 논문을 읽는다고 느끼면 실패."
```

**체크리스트:**
- [ ] "이걸 하면 안 된다"는 경계가 1개 이상 명시됐는가?
- [ ] AI가 흔히 빠지는 함정(진부한 접근, 형식 이탈)을 미리 차단했는가?

---

### Layer 4 — Core Capability: 구조 요구사항

**쓸 것:** 결과물이 갖춰야 할 구체적 구조 요소들. 섹션 순서, 포함 필수 항목, 서술 방식.
이 레이어가 두꺼울수록 Step 3 에이전트의 모호성이 줄고 설계도 정밀도가 올라간다.

```
// 예시 (음악 설계도)
"[화성 진행: 섹션별 코드 진행, 조성적 긴장-해소 구조]
  [리듬 구조: 주 리듬 패턴, 폴리리듬 여부, 박자 변환 지점]
  [멜로디 설계: 주선율 음역·성격·발전 방식, 대위선율 유무]"

// 예시 (RP 캐릭터 시트)
"[외형 묘사: 구체적 수치 포함] [전투 스타일: 기술명 3개 이상]
  [약점: 반드시 극복 가능한 형태여야 함] [배경 서사: 타임라인 형식]"
```

**체크리스트:**
- [ ] 항목별로 "무엇을 어떻게 서술하라"가 명확한가?
- [ ] 각 항목이 독립적으로 검증 가능한가? ("화성 진행이 있는가" → YES/NO)

---

### Layer 5 — Checkpoint: [기대치 정의] 블록

**가장 중요한 레이어.** DIRECTION 안에 `[기대치 정의]` 블록을 명시적으로 삽입한다.
Step 3 에이전트는 이 블록을 읽고 "이 천장을 넘어서라"를 수행한다. 별다른 명시 없을시, 도메인의 최고 전문가 그 이상의 수준으로 정의한다.

**형식:**
```
[기대치 정의]
이 결과물의 기대치는 '___'이다.
구체적으로:
- [검증 기준 1: 특정 독자군의 반응]
- [검증 기준 2: 즉시 사용 가능성]
- [이 프레임 밖으로 나가지 않는다: 금지 형태 명시]
```

**예시 (음악 설계도):**
```
[기대치 정의]
이 결과물의 기대치는 '실제 편곡 즉시 가능한 설계도'이다.
구체적으로:
- 현악 편곡 경험이 있는 작곡가가 읽었을 때 '이 설계도대로 치면 무조건 나온다'가 기준.
- 이 프레임 밖(예: 음악 이론 강의, 막연한 분위기 묘사, 영감 에세이)으로 나가지 않는다.
```

**예시 (요리 레시피):**
```
[기대치 정의]
이 결과물의 기대치는 '레시피를 처음 보는 요리사가 재현 가능한 정밀도'이다.
구체적으로:
- 계량 없이 "적당히"가 한 번도 등장하지 않는 수준이 기준.
- 맛 묘사 에세이가 아니라 조리 지시문이어야 한다.
```

**체크리스트:**
- [ ] `[기대치 정의]` 블록이 DIRECTION 안에 있는가?
- [ ] 기대치가 특정 독자군의 반응으로 서술됐는가?
- [ ] 금지 형태(이 프레임 밖 예시)가 명시됐는가?

---

### Layer 6 — Constraints: 절대 금지사항

마지막 방어선. DIRECTION 안의 "조건:" 섹션 또는 문장으로 삽입한다.
Layer 3(Background)에서 이미 다룬 내용을 여기서 반복하는 것은 허용 — 중요도가 높을수록 반복 가치가 있다.

```
// 예시
"조건: 가사 없음. 각 곡은 완전히 다른 감정 영역을 점령할 것.
  읽으면 즉시 편곡 작업에 착수할 수 있는 수준의 밀도."

"금지: 도입-전개-결론 3단 구조. '~의 미학이다' 식의 추상적 정의문.
  비유가 비유를 설명하는 순환 구조."
```

**체크리스트:**
- [ ] 절대 금지 형태가 1개 이상 있는가?
- [ ] "있어 보이기" 패턴(고유명사 나열, 미사여구 연쇄)을 차단했는가?

---

### DIRECTION 완성 템플릿

```json
"DIRECTION": "[Layer 4: 구조 요구사항 — 항목 열거].

형식: [Layer 1: 포맷 명시].

독자 수준: [Layer 2: 전문성 + 어투].

경계: [Layer 3: 프레임 밖 금지 사항].

조건: [Layer 6: 절대 금지사항].

[기대치 정의]
이 결과물의 기대치는 '[Layer 5: 한 문장 정의]'이다.
구체적으로:
- [검증 기준 1]
- [검증 기준 2]
- 이 프레임 밖([Layer 5: 금지 형태 예시])으로 나가지 않는다."
```

---

## 6. STARTING_SENTENCE — 씨앗 설계 원칙

`STARTING_SENTENCE`는 답이 아니라 **에너지의 방향**이다.
Step 1 에이전트가 이 문장을 무작위 단어와 충돌시켜 첫 체이닝을 시작한다.

### 좋은 씨앗 vs 나쁜 씨앗

| 기준 | 좋은 씨앗 | 나쁜 씨앗 |
|------|----------|----------|
| **개방성** | 질문이거나 미완의 진술 | 답이 포함된 문장 |
| **구체성** | 감각적으로 포착 가능한 이미지 | 추상 개념의 정의문 |
| **충돌 가능성** | 무작위 단어가 부딪힐 표면이 많음 | 이미 완결된 진술 |

```
// 좋은 예
"무도회의 바닥이 진동하는 방식—발끝이 아니라 뼛속까지 전달되는 음악은 어떻게 생겼는가."
 → 미완의 질문 + 감각 이미지 + 무작위 단어가 어디서든 부딪힐 수 있음

// 나쁜 예
"뉴에이지 무도회 음악은 클래식과 현대적 요소의 융합이다."
 → 이미 답이 있음. 체이닝이 이 문장을 반복하게 됨.

// 나쁜 예
"창의성이란 무엇인가."
 → 너무 추상적. 충돌 표면이 없음. 모든 무작위 단어가 어색하게 붙음.
```

### 씨앗 작성 패턴

```
패턴 1 — 역설적 관찰
"[X]하는 방식이 [Y]처럼 느껴진다면, [Z]는 어디에 있는가."

패턴 2 — 감각적 단절
"[구체적 감각 현상]—[뒤따르는 충돌 이미지]."

패턴 3 — 미완의 조건문
"[전제 상황]이라면, [열린 질문]?"
```

---

## 7. MANDATORY_WORD — 오염 제어

`MANDATORY_WORD`는 모든 체이닝 문장에 강제 삽입되는 단어다.
Step 1, 2에서 에이전트는 이 단어들을 반드시 포함시켜야 한다.

### 혼합 비율 원칙

**도메인 내부 용어 50% + 도메인 외부 용어 50%**

| 유형 | 효과 | 과도하면 |
|------|------|----------|
| 도메인 내부 용어만 | 전문적이지만 뻔한 패턴 | 기존 전문가 지식의 재서술로 수렴 |
| 도메인 외부 용어만 | 창의적이지만 맥락 붕괴 | 의미 없는 무작위성 |
| 50:50 혼합 | 친숙한 맥락 + 이질적 충격 | — |

```
// 음악 도메인 예시
내부: "공명", "무게중심"
외부: "파열"  ← 음악에서 흔히 쓰이지 않는 단어

// 요리 도메인 예시
내부: "발효", "열전도"
외부: "붕괴"  ← 요리 용어가 아닌 물리/구조 용어

// 철학 도메인 예시
내부: "인과", "존재"
외부: "균열"  ← 지질학 또는 공학 용어
```

### 단어 수 권장

- 2~4개: 연결이 자유롭고 품질 유지 용이
- 5개 이상: 문장당 밀도가 높아지지만 자연스러움 하락 위험

---

## 8. PREFERRED_IMAGERY — 개념 방향벡터

`PREFERRED_IMAGERY`는 "이 개념 렌즈로 무작위 단어 충돌을 해석하라"는 방향벡터다. 감각 이미지 묘사가 아니라 **개념적 중력장**이다.

### 역할

Step 1에서 Python이 뽑은 무작위 단어는 충돌의 재료다. PREFERRED_IMAGERY는 그 충돌이 어떤 방향으로 해석되는지를 결정한다.

```
무작위 단어 "균열" + IMAGERY "현악의 마찰열"
→ "현악이 균열을 만들어내는 방식—마찰이 쌓이면 먼저 끊어지는 것은 활이 아니라 공기다"

무작위 단어 "균열" + IMAGERY "아름다움"
→ "아름다운 균열..." → 방향 없음, 무작위 단어가 흡수되지 않음
```

### 좋은 키워드 두 가지 유형

**유형 1 — 도메인 교차 (Cross-domain translation)**

도메인 A의 메커니즘을 도메인 B의 언어로 번역한다. 번역 간극이 충돌 에너지가 된다.

```
음악 × 열역학: "현악의 마찰열"
음악 × 역학:   "왈츠의 원심력"
음악 × 광학:   "달빛 굴절"
요리 × 역학:   "발효의 압력 곡선"
철학 × 지질학: "개념의 단층면"
```

**유형 2 — 임계 상태 (Edge states)**

변화·전환·붕괴 직전. 방향성이 내재되어 있어 무작위 단어와 충돌하면 즉시 긴장이 생긴다.

```
"정지 직전의 숨"
"포화 직전의 용액"
"임계점의 금속"
"유리 위 물방울"   ← 표면장력이 깨지기 직전
```

### 나쁜 키워드

| 유형 | 예시 | 이유 |
|------|------|------|
| Pseudo-profound bullshit | "존재의 울림", "우주적 감응", "내면의 진실" | 아래 경계 기준 참조 |
| DIRECTION 요약 | "작곡을 위한 감성" | 중복 주입, 충돌 없음 |

### ⚠️ Pseudo-profound bullshit 경계 기준

심오해 보이지만 충돌 방향을 만들지 못하는 키워드. PREFERRED_IMAGERY에서 가장 흔한 함정이다.

**시는 시적 이미지가, 철학은 논리 고리가 있다. PPB는 그 둘 다 없다.**

판별 테스트 — 하나라도 해당하면 PPB:

1. **교체 테스트**: 비슷하게 '심오한' 단어로 교체해도 의미가 동일한가?
   → "존재의 울림" → "내면의 공명" → "영혼의 진동" — 교체해도 차이 없으면 PPB

2. **메커니즘 테스트**: 이 키워드와 연결되는 물리·논리·감각 고리를 한 문장으로 말할 수 있는가?
   → "현악의 마찰열": 활과 현의 접촉면에서 열이 발생한다 → ✅
   → "삶의 깊은 울림": ??? → ❌

3. **방향 테스트**: 옆에 무작위 단어를 붙였을 때 특정 방향이 생기는가?
   → "아름다운" + "벼랑" → 어느 방향이든 가능 → ❌

경계 사례:

| 키워드 | 판정 | 이유 |
|--------|------|------|
| "정지 직전의 숨" | ✅ | 물리적 임계 상태 — 메커니즘 있음 |
| "결핍의 구조" | ✅ | 철학·경제 논리 고리 있음 (도메인 맞을 때) |
| "무게감", "날선" | ✅ | 물리 감각 참조점 있음 — 카피라이팅에서 충분히 작동 |
| "영원한 아름다움" | ❌ | 심오해 보이지만 메커니즘 없음 |
| "우주적 진실" | ❌ | 교체 테스트 즉시 탈락 |

### 키워드 작성 방법

```
1. 도메인의 핵심 메커니즘 2~3개 추출
   → 음악: 진동, 마찰, 공명

2. 각 메커니즘을 완전히 다른 도메인 언어로 번역
   → 진동 × 지질학 = "단층 진동"
   → 마찰 × 열역학 = "마찰열"
   → 공명 × 건축학 = "공명 공간"

3. 임계 상태 1~2개 추가
   → "정지 직전의 숨", "단선 직전의 장력"

4. 각 키워드 자문: "이 단어 옆에 무작위 단어를 붙였을 때 방향이 생기는가?"
   → "현악의 마찰열" + "벼랑" → O (긴장 방향 있음)
   → "아름다움" + "벼랑" → X (어느 방향이든 가능)
```

**수량:** 3~6개. 많을수록 중력장이 분산되어 방향성이 약해진다.

---

## 9. 수량 파라미터 보정표

| 목적 | CHAINS_COUNT | SELECTION_B_COUNT | REFINING_COUNT | MODE |
|------|-------------|------------------|---------------|------|
| 빠른 프로토타입 | 100~150 | 20~30 | 2~3 | NUANCE |
| 표준 결과물 | 250~350 | 50~70 | 3~5 | NUANCE |
| 희귀 연결 탐색 | 500~700 | 100~150 | 5~8 | CHAOS |
| 최대 발산 후 수렴 | 800+ | 150~200 | 2~5 | CHAOS |

**비율 규칙:**
- `SELECTION_B_COUNT` = `CHAINS_COUNT` × 0.20 ~ 0.30
- `REFINING_COUNT` = 최종 사용할 결과물 개수 그대로
- CHAOS 모드는 희귀 연결 확률이 높지만 쓸 수 없는 문장도 많음 → `SELECTION_B_COUNT` 낮추지 말 것

---

## 10. Anti-Pattern 목록

### DIRECTION에서 흔히 나타나는 실패 패턴

| 패턴 | 증상 | 수정 방향 |
|------|------|-----------|
| **감성 오버로드** | 분위기 묘사만 있고 구조 요구사항이 없음 | Layer 4 추가 (항목 열거) |
| **기대치 없음** | `[기대치 정의]` 블록 미존재 | Layer 5 삽입 |
| **프레임 없음** | "이거 하면 안 된다"가 없음 | Layer 3, 6 추가 |
| **독자 불명확** | 어투와 전문성 수준 미지정 | Layer 2 추가 |
| **형식 미지정** | 최종 결과물의 포맷이 없음 | Layer 1 강화 |
| **범위 과다** | 한 DIRECTION에 여러 개의 독립 결과물 요구 | CHAINS_COUNT 늘리기 |

### STARTING_SENTENCE에서 흔히 나타나는 실패 패턴

| 패턴 | 예시 | 수정 방향 |
|------|------|-----------|
| **완결된 진술** | "X는 Y이다." | 미완 질문으로 전환 |
| **과도한 추상** | "존재의 의미란." | 감각 이미지 삽입 |
| **DIRECTION 복사** | DIRECTION을 요약한 문장 | 씨앗과 도착점을 다른 층에 분리 |

### MANDATORY_WORD에서 흔히 나타나는 실패 패턴

| 패턴 | 증상 | 수정 방향 |
|------|------|-----------|
| **전부 내부 용어** | 뻔한 전문 텍스트 재생산 | 외부 용어 50% 교체 |
| **과도하게 많음** | 5개 초과 → 문장이 억지스러워짐 | 3~4개로 압축 |
| **서로 같은 도메인** | 3개가 모두 음악 용어 | 도메인 분산 |

---

## 11. 완성 예시 — 단계별 비교

### 예시: 요리 창작 가이드 (개선 전→후)

**Before (레이어 없음):**
```json
{
  "STARTING_SENTENCE": "요리는 창의성이다.",
  "MANDATORY_WORD": ["맛", "레시피", "재료"],
  "PREFERRED_IMAGERY": ["맛있는", "신선한"],
  "CHAINS_COUNT": 50,
  "MODE_SELECTION": "NUANCE",
  "SELECTION_B_COUNT": 10,
  "REFINING_COUNT": 2,
  "DIRECTION": "혁신적인 요리 레시피를 만들어줘.",
  "FINAL_LANGUAGE": "Korean"
}
```
→ Step 3 결과: 뻔한 레시피 2개. "신선한 재료를 사용하세요" 수준.

---

**After (6-Layer 적용):**
```json
{
  "STARTING_SENTENCE": "발효가 멈추는 순간—온도가 아니라 시간이 맛을 결정한다면, 레시피는 어디서 끝나는가.",
  "MANDATORY_WORD": ["열전도", "발효", "붕괴"],
  "PREFERRED_IMAGERY": [
    "재료가 서로를 침식하는 구간",
    "조리가 아니라 해체",
    "맛의 붕괴점"
  ],
  "CHAINS_COUNT": 150,
  "MODE_SELECTION": "CHAOS",
  "SELECTION_B_COUNT": 30,
  "REFINING_COUNT": 2,
  "STEP1_EXECUTOR": "SELF",
  "DIRECTION": "기존에 없는 요리 개념 2개. 형식: 각 개념당 [개념명 + 한 줄 정의] [핵심 원리: 왜 맛이 달라지는가] [실행 지시: 단계별 조리법, 계량 포함] [실패 조건: 이 방식이 무너지는 지점] 포함. 독자 수준: 조리 경험 3년 이상의 셰프. 용어는 전문 조리 용어 직접 사용. 경계: 기존 퓨전 요리 개념 재서술 금지. 맛 묘사 에세이 금지. 조건: 각 개념은 서로 완전히 다른 물리-화학적 원리에 기반할 것. [기대치 정의] 이 결과물의 기대치는 '조리 경험 있는 셰프가 읽고 즉시 실험에 착수하는 수준'이다. 구체적으로: 재현 가능한 계량과 단계가 있어야 하며, '이런 방식은 생각 못 했는데 원리가 납득된다'가 반응 기준. 에세이, 개념 설명, 이론 강의 형태로 나가지 않는다.",
  "FINAL_LANGUAGE": "Korean",
  "LANGUAGE_RULE": "NO_3_CONSECUTIVE_FOREIGN_WORDS"
}
```
→ Step 3 결과: 전문 셰프가 실험 가능한 새로운 조리 개념 2개 (구체적 계량 + 실패 조건 포함).

---

## 12. 격리 모델 (server-side, 정보용)

서버는 agent_id별로 격리된 workspace를 자체 관리한다. **클라이언트는 도구 호출만 하면 되며, 아래 경로들은 정보용**이다 (직접 접근 불필요).

```
delutionist/                          # 서버 측 base_dir
├── output/agents/<id>/
│   ├── section_a_chains.txt
│   ├── section_b_refined.txt
│   └── section_c_final.txt
├── staging/agents/<id>/
│   ├── state.json                    # 진행 상태 (get_status로 조회)
│   ├── append.lock / state.lock / config.lock
│   ├── step1_gemini_prompt.txt
│   └── worker_*_prompt.txt
├── input/agents/<id>/
│   └── request.json                  # update_request_config가 갱신
├── mini/staging/agents/<id>/
│   ├── step1_worker_*.txt
│   └── step1_1_worker_*.txt
└── staging/agents/registry.json      # 모든 agent 메타 + TTL
```

agent_id별 격리이므로 여러 클라이언트가 동시에 사용해도 작업물·상태가 섞이지 않는다.

---

## 13. 상태 관리 (도구 관점)

진행 상태는 서버가 agent별로 자동 관리한다. 클라이언트는 도구로만 조회·조작한다:

| 작업 | 도구 호출 |
|---|---|
| **현재 진행 조회** | `get_status(agent_id)` → `{current_step, progress: {step1_chains: "120/200", step2_refined: "...", step3_final: "..."}, mode, starting_sentence}` |
| **다음 step 자동 진입** | 줄 수가 CHAINS_COUNT / SELECTION_B_COUNT에 도달하면 `run_delusionist` 호출 시 자동 전환. |
| **Step 3 완료 신호** | `append_result(agent_id, step="3", content=..., finalize=true)` — Step 3는 줄 수가 아니라 명시적 `finalize=true`로만 완료 처리. |
| **초기화** | `reset_factory(agent_id, confirm=true)` — output/staging 비움, request.json은 보존. |
| **agent 자체 폐기** | `release_agent(agent_id)` — 4개 디렉토리 통째 삭제 + 레지스트리에서 제거. |

---

## 14. 단어 풀과 언어 감지

- FINAL_LANGUAGE가 "Korean"이면 → `input/extracted_words.txt` (한국어 풀) 사용
- FINAL_LANGUAGE가 "English"이면 → `input/100000word.txt` (영어 풀) 사용
- 명시 안 하면 → STARTING_SENTENCE + DIRECTION 텍스트에서 한글 포함 여부로 자동 감지

단어 추출은 `linecache`를 사용해 랜덤 줄 번호로 직접 접근한다 (24MB 파일을 통째로 메모리에 올리지 않음).

---

## 15. 환경변수 (선택)

| 변수 | 기능 | 기본값 |
|------|------|--------|
| DELUSIONIST_GEMINI_MODEL | Gemini CLI 사용 시 모델 지정 | (CLI 기본) |
| DELUSIONIST_STEP1 | Step 1 실행 방식 오버라이드 ("GEMINI_CLI" / "SELF") | request.json 값 |
| DELUSIONIST_STEP1_ETA_OVERHEAD_S | ETA 계산 오버헤드 (초) | 20 |
| DELUSIONIST_STEP1_ETA_S_PER_LINE | ETA 계산 줄당 소요 (초) | 1.2 |

---

## 16. 주의사항

1. **`run_delusionist`는 한 번 호출에 한 배치 진행 후 반환된다.** 전체 파이프라인을 끝내려면 반복 호출이 정석.
2. **`STEP1_EXECUTOR="GEMINI_CLI"`(기본) 모드에선 Agent가 직접 문장을 만들지 않는다.** `run_delusionist` 응답에 prompt path와 gemini cmd가 들어 있으니, Bash로 실행 후 그 결과만 `append_result`로 올린다.
3. **Step 진행은 줄 수 기반.** 파일 줄 수가 목표치(CHAINS_COUNT / SELECTION_B_COUNT)에 도달하면 다음 step으로 자동 전환. 단 Step 3은 예외 — 명시적 `finalize=true`까지 들어와야 완료.
4. **`MANDATORY_WORD`는 Step 1~2에서만 강제된다.** Step 3에서는 자연스럽게 녹아든 형태로만.
5. **append만 — 덮어쓰기 없음.** 도구 차원에서 보장 (`append_result`만 노출). 처음부터 다시 가려면 `reset_factory(agent_id, confirm=true)`.
6. **agent_id 만료**: 24h 무활동 시 workspace 자동 삭제 + 다음 호출 거부 → 새로 `register_agent`. 도구 호출이 활동으로 간주되어 매번 expiry가 갱신된다 (sliding window).

---

## 17. 빠른 참조 체크리스트

```
request.json 제출 전 점검:

STARTING_SENTENCE
  □ 미완의 질문이거나 감각 이미지를 포함하는가?
  □ DIRECTION의 요약/복사가 아닌가?

MANDATORY_WORD
  □ 2~4개인가?
  □ 도메인 내부/외부 혼합인가?

PREFERRED_IMAGERY
  □ 도메인 교차, 임계 상태, 또는 물리·감각 참조점이 있는 개념인가?
  □ PPB 교체 테스트 통과: 비슷한 심오한 단어로 바꿔도 차이 없으면 탈락
  □ 무작위 단어와 충돌했을 때 방향이 생기는가?

DIRECTION
  □ Layer 1: 포맷과 포함 항목이 명시됐는가?
  □ Layer 2: 독자 전문성과 어투가 명시됐는가?
  □ Layer 3: 프레임 경계(금지 형태)가 있는가?
  □ Layer 4: 구조 요소가 항목별로 열거됐는가?
  □ Layer 5: [기대치 정의] 블록이 있는가?
  □ Layer 6: 절대 금지사항이 명시됐는가?

수량
  □ SELECTION_B_COUNT ≈ CHAINS_COUNT × 0.2~0.3
  □ REFINING_COUNT = 실제 필요한 최종 결과물 수
```

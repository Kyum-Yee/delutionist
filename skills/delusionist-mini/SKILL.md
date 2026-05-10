---
name: delusionist-mini
description: Brainstormer-only variant. Runs Step 1 (Stochastic Context Pollution chaining) + Step 1-1 (PPB filter + idea conversion) on the remote MCP backend, then stops. Output is a flat list of surviving idea fragments.
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
> 원격 MCP endpoint: `https://kyumyee-playground.onrender.com/delusionist/mcp/`
> Step 2(refining)·Step 3(final shaping)는 **수행하지 않는다.** 산출물은 살아남은 아이디어 조각 N줄.

---

## 1. 무엇이 다른가 — `/delusionist` 와의 차이

| 항목 | `/delusionist` (풀) | `/delusionist-mini` (이 스킬) |
|------|--------------------|-----------------------------|
| 단계 | Step 1 → 1-1 → 2 → 3 | Step 1 → 1-1에서 종료 |
| request.json | ✓ (서버가 agent별 보존) | ✗ (도구 인자로 직접 전달, stateless) |
| 산출물 | REFINING_COUNT개의 완성형 결과물 | 출제·창작·탐색용 아이디어 조각 N줄 |
| 도구 | 13개 + 라이프사이클 3 + 스킬 2 | 4개 + 라이프사이클 |
| 용도 | 완성형 가공물 생성 | 브레인스토밍 / 후속 가공 전 단계 재료 |

핵심 의도: 다음 단계로 가공할 **재료**가 필요할 때 쓴다. 가공기가 따로 있거나, 손으로 가공하고 싶거나, 충돌 풀만 보고 싶을 때.

---

## 2. 도구 4개 (+ 라이프사이클 2)

| 도구 | 역할 | agent_id |
|------|------|---|
| `register_agent` | 첫 호출. agent_id 발급 + 격리 workspace. 24h 슬라이딩 TTL | — |
| `prepare_mini_step1_workers` | Step 1 워커 분할. chains_count, chains_per_worker, direction, starting, mandatory, imagery, mode, final_language, executor, model 인자 | ✓ |
| `parse_mini_step1_response` | Gemini stdout(또는 SELF 출력)에서 3자리 숫자로 시작하는 줄만 추출 → chain 배열. stateless | — |
| `prepare_mini_step1_1_workers` | Step 1-1 워커 분할. chains 배열 + direction + mandatory + final_language + executor 인자 | ✓ |
| `parse_mini_step1_1_response` | Gemini 응답에서 idea 줄 추출 (번호·따옴표·볼드 제거). stateless | — |
| `release_agent` | 작업 종료 후 정리 (선택; TTL이 자동 정리) | ✓ |

### `executor` 두 모드

| 값 | 동작 | 응답 핵심 필드 |
|---|---|---|
| `"GEMINI"` (기본) | 도구가 워커별 cmd를 만들어 반환. 클라이언트가 Bash로 N개 병렬 실행 후 stdout을 `parse_mini_*`로 파싱 | `workers[*].cmd` + `prompt` |
| `"SELF"` | 도구가 워커별 prompt 본문만 반환 (cmd 없음). 클라이언트(에이전트)가 prompt 따라 직접 `line_count` 줄 생성 | `workers[*].prompt` |

---

## 3. 표준 워크플로우

```text
0. register_agent → agent_id 보관

1. resp1 = prepare_mini_step1_workers(
     agent_id,
     chains_count=N,             # 생성할 총 chain 수 (목표 idea × 1.25 권장)
     chains_per_worker=25,
     direction="...",            # 6-Layer 직접 작성 (§5 참조)
     starting="...",             # 미완 질문 / 감각 이미지
     mandatory=["..."],          # 도메인 내부 50% + 외부 50%, 2~4개
     imagery=["..."],            # 도메인 교차 / 임계 상태 키워드 3~6개
     mode="NUANCE"|"CHAOS",
     final_language="Korean"|"English"|"Auto",
     executor="GEMINI"|"SELF"
   )

2. # 워커 실행
   stdouts1 = []
   for w in resp1.workers:
       if executor == "GEMINI":
           stdout = bash(w.cmd)             # gemini --output-format json ...
       else:  # SELF
           stdout = agent_generate(w.prompt, line_count=w.line_count)
       stdouts1.append(stdout)

3. chains = []
   for raw in stdouts1:
       chains.extend(parse_mini_step1_response(raw))

4. resp2 = prepare_mini_step1_1_workers(
     agent_id,
     chains=chains,              # Step 1 결과 그대로 전달
     chains_per_worker=25,
     direction="...",            # Step 1과 동일 DIRECTION
     mandatory=["..."],
     final_language=...,
     executor="GEMINI"|"SELF"
   )

5. # Step 1-1 워커 실행 (Step 1과 동일 패턴)
   stdouts2 = ...

6. ideas = []
   for raw in stdouts2:
       ideas.extend(parse_mini_step1_1_response(raw))

7. # 클라이언트가 한 줄당 한 아이디어 형식으로 직접 저장
   open("ideas.md", "w").write("\n".join(ideas))

8. (선택) release_agent(agent_id)
```

`chains_count` 권장: 목표 idea 수의 1.25배 (Step 1-1이 ~1/5 폐기). 예: idea 25개 원하면 `chains_count=32`.

---

## 4. 출력 형식

`parse_mini_step1_1_response` 반환 형식:
```json
[
  "<라인 1 아이디어>",
  "<라인 2 아이디어>",
  "..."
]
```

- 한 줄 = 하나의 아이디어 (1~2 짧은 문장)
- 마크다운·번호·따옴표·볼드 없음 (parser가 떼고, 프롬프트가 평문 강제)
- 폐기된 PPB 체인은 흔적 없이 제거됨

후속으로 손 가공·다른 파이프라인 투입 모두 자유.

---

## 5. 6-Layer 참조 — `direction` 작성 시

mini는 Step 3이 없으므로 `direction`은 "최종 결과물 스펙"이 아니라 **"아이디어 조각이 어떤 도메인 어휘로 번역되어야 하는가"의 가이드**로 해석된다. 6-Layer 작성 기준은 [delusionist SKILL.md](../delusionist/SKILL.md) §5~§8과 동일.

| 인자 | 작성 기준 |
|------|-----------|
| `direction` | 6-Layer 풀 적용. Layer 1(형식)·Layer 2(독자)·Layer 3(경계)·Layer 4(구조)·Layer 5([기대치 정의] 블록 필수)·Layer 6(절대 금지). 자유 서술 도메인을 그대로 반영. |
| `starting` | 미완 질문 또는 감각 이미지 패턴. DIRECTION 요약·복사 금지. 충돌 표면 풍부. |
| `mandatory` | 도메인 내부 50% + 외부 50% 혼합, 2~4개. |
| `imagery` | 도메인 교차 또는 임계 상태 키워드 3~6개. PPB 키워드 금지. 도메인이 너무 명확해 충돌 렌즈가 불필요하면 빈 리스트. |
| `mode` | 발산 폭이 필요하면 `CHAOS`, 자연스러운 충돌만 원하면 `NUANCE`. 기본 `NUANCE`. |

---

## 6. 에러 패턴 / 주의

| 응답 | 의미 | 대응 |
|---|---|---|
| `ERROR: 'agent_id' is required.` | 첫 호출에 register_agent 누락 | `register_agent` 먼저 |
| `ERROR: agent_id 'a-...' is unknown or expired.` | 24h 무활동 만료 / 모르는 ID | 새로 `register_agent` |
| `ERROR: chains must be a non-empty list` | Step 1-1 호출에 chains 비어 있음 | `parse_mini_step1_response` 결과 재확인 |
| `ERROR: split_step1*_workers failed: ...` | 인자 검증 실패 | `direction` 필수, `chains_count >= 1` |

기타 주의:
1. `chains_count`가 `chains_per_worker`의 배수가 아니어도 OK — 마지막 워커가 남은 줄 수를 가져간다.
2. `mandatory`는 Step 1에서만 강제된다. Step 1-1 출력에는 자연스럽게 녹아든 형태로만 남음.
3. Gemini 호출은 N개 워커 병렬로 나간다. 응답 파싱 실패 시 그 워커 분량은 0이 되므로 실제 산출이 목표 미만일 수 있다 — 그 워커만 재실행.
4. `final_language="Auto"`면 `starting + direction`(Step 1) 또는 `chains` 일부(Step 1-1) 텍스트로 한국어 자동 감지.
5. agent_id 만료: 24h 무활동 시 workspace 자동 삭제. 도구 호출이 활동으로 간주되어 매번 expiry가 갱신된다 (sliding window).

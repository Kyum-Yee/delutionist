"""Delusionist Step 1 + Step 1-1 — 압축 이식판.

원본 main.py에서 다음을 발췌:
- _build_step1_gemini_prompt (Step 1 chaining 프롬프트 빌더)
- MODE_DEFINITIONS / _describe_mode
- get_random_words_from_file (linecache 기반)
- _resolve_language_and_pool / _is_korean
- prepare_parallel_gemini_workers의 워커 분할 로직

추가:
- build_step1_1_prompt (Step 1-1 PPB 폐기 + 아이디어 변환)
- parse_step1_response / parse_step1_1_response (gemini --output-format json 파서)
"""
from __future__ import annotations

import json
import linecache
import random
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).parent
PROMPTS_DIR = BASE_DIR / "prompts"

# 워드풀 — 사전 계산된 줄 수 상수 (24MB 파일을 메모리에 안 올리기 위함)
WORD_POOLS: dict[str, tuple[Path, int]] = {
    "Korean": (BASE_DIR / "extracted_words.txt", 917273),
    "English": (BASE_DIR / "100000word.txt", 466551),
}

# Step 1 동사-논항 호응 축 (원본 그대로)
MODE_DEFINITIONS: dict[str, str] = {
    "CHAOS": (
        "CHAOS mode — verb-argument coherence constraint LIFTED. In each sentence, the verb "
        "is not required to semantically agree with its arguments (subject and object); it "
        "may, but it does not have to. Verb-argument incoherence is permitted but never "
        "required. There are no additional constraints on non-verbal sentence components "
        "either."
    ),
    "NUANCE": (
        "NUANCE mode — verb-argument coherence required. In each sentence, the verb MUST "
        "semantically agree with its arguments (subject and object): the action it names is "
        "actually applicable to its subject and object. Incoherence in the verb-argument "
        "binding is forbidden. Any incoherence, if present, must lie only among non-verbal "
        "sentence components (modifiers, settings, co-occurring entities). Sentences in "
        "which all components fully cohere are also acceptable."
    ),
}


def describe_mode(mode: str) -> str:
    return MODE_DEFINITIONS.get(mode.strip().upper(), MODE_DEFINITIONS["NUANCE"])


def detect_language(text: str, final_language: str = "") -> str:
    """STARTING_SENTENCE + DIRECTION 텍스트와 FINAL_LANGUAGE 명시값으로 언어 결정."""
    final = (final_language or "").strip().upper()
    if final == "KOREAN":
        return "Korean"
    if final == "ENGLISH":
        return "English"
    if re.search(r"[가-힣]", text or ""):
        return "Korean"
    return "English"


def get_random_words(pool_path: Path, total_lines: int, count: int = 3) -> list[str]:
    """linecache로 랜덤 줄 번호만 뽑아 단어 추출. 파일 전체 로드 안 함."""
    if total_lines <= 0:
        return []
    target = random.sample(range(1, total_lines + 1), min(count, total_lines))
    words: list[str] = []
    for ln in target:
        s = linecache.getline(str(pool_path), ln).strip()
        if s:
            words.append(s)
    return words


@dataclass(frozen=True)
class DelutionistConfig:
    direction: str
    starting: str
    mandatory: list[str]
    imagery: list[str]
    mode: str  # "CHAOS" | "NUANCE"
    final_language: str  # "Korean" | "English"
    language_rule: str = "NO_3_CONSECUTIVE_FOREIGN_WORDS"


# ─── Step 1 프롬프트 빌더 (원본 main.py의 _build_step1_gemini_prompt 복원) ──

def build_step1_prompt(
    *,
    cfg: DelutionistConfig,
    batch_start: int,
    batch_random_words: list[list[str]],
) -> str:
    """원본 _build_step1_gemini_prompt 그대로 — 한국어 출력 가정 + 출력 포맷 strict."""
    lines: list[str] = []
    lines.append(
        "You are generating creative sentences for a pipeline step called STEP 1 (CHAINING)."
    )
    lines.append("")
    lines.append("OUTPUT FORMAT (STRICT):")
    lines.append(f"- Return exactly {len(batch_random_words)} lines.")
    lines.append(
        "- Each line starts with a 3-digit number (e.g., 001, 002...) followed by a period and space, then the sentence."
    )
    lines.append(
        "- In each sentence, wrap the random words (or their domain-adapted variants) in markdown bold (**word**). At least 3 bold words per line."
    )
    lines.append(
        "- End each sentence with a parenthetical annotation: what collision emerged, what direction it could go. e.g., (충돌: 빙하+요리 → 느린 해동 조리법 가능성)"
    )
    lines.append("- No titles, no explanations, no extra blank lines.")
    lines.append("")
    lines.append("MODE:")
    lines.append(f"- {describe_mode(cfg.mode)}")
    lines.append("")
    lines.append("CONSTRAINTS:")
    if cfg.mandatory:
        lines.append(
            f"- Every line MUST include ALL mandatory words exactly as written: {', '.join(cfg.mandatory)}"
        )
    if cfg.language_rule:
        lines.append(f"- LANGUAGE_RULE: {cfg.language_rule}")
    if cfg.imagery:
        lines.append(f"- Prefer imagery motifs: {', '.join(cfg.imagery)}")
    lines.append(
        "- The sentences should feel like a surreal collision (bold, unexpected connections); naturalness is governed by the MODE rule above."
    )
    lines.append(
        "- Random words are for context pollution: you may replace them with context-fitting variants if needed, but keep the 'collision' spirit."
    )
    lines.append("")
    lines.append("CONTEXT:")
    if cfg.direction:
        lines.append("DIRECTION:")
        lines.append(cfg.direction.strip())
        lines.append("")
    if cfg.starting:
        lines.append("STARTING_SENTENCE (seed tone/energy, do not copy verbatim if awkward):")
        lines.append(cfg.starting.strip())
        lines.append("")
    lines.append("RANDOM WORDS PER LINE:")
    for idx, words in enumerate(batch_random_words, start=batch_start):
        joined = ", ".join(words)
        lines.append(f"- Line {idx}: {joined}")
    lines.append("")
    lines.append("Now produce the lines.")
    return "\n".join(lines).strip() + "\n"


# ─── Step 1-1 프롬프트 빌더 (NEW — PPB 폐기 + 아이디어 변환) ────────────────

def build_step1_1_prompt(
    *,
    cfg: DelutionistConfig,
    chains: list[str],
    discard_count: int,
) -> str:
    """staging의 step1_1.txt 템플릿에 변수 주입."""
    template_path = PROMPTS_DIR / "step1_1.txt"
    template = template_path.read_text(encoding="utf-8")
    chains_block = "\n".join(f"{i:03d}. {c}" for i, c in enumerate(chains, 1))
    keep = max(0, len(chains) - discard_count)
    return template.format(
        DIRECTION=cfg.direction.strip(),
        MANDATORY_WORD=", ".join(cfg.mandatory) if cfg.mandatory else "(없음)",
        FINAL_LANGUAGE=cfg.final_language,
        COUNT_IN=len(chains),
        COUNT_OUT=keep,
        DISCARD=discard_count,
        CHAINS_BLOCK=chains_block,
    )


# ─── Gemini CLI 명령 + 응답 파서 ──────────────────────────────────────────

def build_gemini_cmd(prompt_path: Path, model: str = "") -> str:
    """staging 프롬프트 파일을 gemini CLI에 던지는 셸 명령."""
    if model:
        return (
            f'gemini --output-format json --model {shlex.quote(model)} '
            f'"$(cat {shlex.quote(str(prompt_path))})"'
        )
    return f'gemini --output-format json "$(cat {shlex.quote(str(prompt_path))})"'


def _extract_response_text(raw: str) -> str:
    """gemini --output-format json 응답에서 response 본문만 추출. JSON 실패 시 원본 그대로."""
    if not raw:
        return ""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return raw
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict) and isinstance(data.get("response"), str):
        return data["response"]
    return raw


_LEADING_ENUM_RE = re.compile(r"^\s*(?:[-*•]|\(?\d+[\).\]]|\d+\.|\d+\))\s+")


def parse_step1_response(raw: str) -> list[str]:
    """3자리 숫자로 시작하는 줄만 chain으로 채택 (Step 1 출력 포맷)."""
    text = _extract_response_text(raw)
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if len(s) > 4 and s[:3].isdigit():
            out.append(s)
    return out


def parse_step1_1_response(raw: str) -> list[str]:
    """평문 한 줄당 하나. 모델이 흘린 번호·따옴표·마크다운 흔적은 제거."""
    text = _extract_response_text(raw)
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        s = _LEADING_ENUM_RE.sub("", s).strip()
        s = s.strip(' "\'`')
        # 마크다운 볼드 흔적 제거
        s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
        if s:
            out.append(s)
    return out


# ─── 워커 분할 로직 (원본 prepare_parallel_gemini_workers 발췌) ──────────

@dataclass(frozen=True)
class WorkerSpec:
    worker_id: int
    line_count: int
    prompt_path: Path
    cmd: str


def split_step1_workers(
    *,
    cfg: DelutionistConfig,
    total_chains: int,
    chains_per_worker: int,
    staging_dir: Path,
    model: str = "",
) -> list[WorkerSpec]:
    """Step 1: total_chains를 chains_per_worker씩 N개 워커로 분할 → 프롬프트 파일 + cmd 생성."""
    if total_chains <= 0:
        return []
    pool_path, total_lines = WORD_POOLS[cfg.final_language]
    staging_dir.mkdir(parents=True, exist_ok=True)

    workers: list[WorkerSpec] = []
    line_offset = 0
    wid = 1
    while line_offset < total_chains:
        wcount = min(chains_per_worker, total_chains - line_offset)
        random_words = [get_random_words(pool_path, total_lines, 3) for _ in range(wcount)]
        prompt = build_step1_prompt(
            cfg=cfg,
            batch_start=line_offset + 1,
            batch_random_words=random_words,
        )
        prompt_path = staging_dir / f"step1_worker_{wid}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        workers.append(
            WorkerSpec(
                worker_id=wid,
                line_count=wcount,
                prompt_path=prompt_path,
                cmd=build_gemini_cmd(prompt_path, model=model),
            )
        )
        line_offset += wcount
        wid += 1
    return workers


def split_step1_1_workers(
    *,
    cfg: DelutionistConfig,
    chains: list[str],
    chains_per_worker: int,
    staging_dir: Path,
    model: str = "",
) -> list[WorkerSpec]:
    """Step 1-1: chains를 chains_per_worker씩 chunk → 각 chunk의 1/5 폐기 지시."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    workers: list[WorkerSpec] = []
    wid = 1
    for start in range(0, len(chains), chains_per_worker):
        chunk = chains[start : start + chains_per_worker]
        if not chunk:
            continue
        discard = max(1, len(chunk) // 5)
        prompt = build_step1_1_prompt(cfg=cfg, chains=chunk, discard_count=discard)
        prompt_path = staging_dir / f"step1_1_worker_{wid}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        workers.append(
            WorkerSpec(
                worker_id=wid,
                line_count=len(chunk) - discard,
                prompt_path=prompt_path,
                cmd=build_gemini_cmd(prompt_path, model=model),
            )
        )
        wid += 1
    return workers

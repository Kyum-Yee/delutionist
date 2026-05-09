"""run_mini.py — Delusionist Mini 러너.

흐름:
  request.json 읽기
    → Step 1 (gemini 병렬 워커, 망상 chain 생성)
    → Step 1-1 (gemini 병렬 워커, PPB 1/5 폐기 + 출제 아이디어 변환)
    → output/ideas_YYYY-MM-DD_HH-MM.md (한 줄 = 한 아이디어)

queue.json·Step 2·Step 3은 만들지 않는다 — 살아남은 아이디어 조각만 단일 파일로 떨군다.

사용법:
  cd /Users/jakesmacair/프로젝트\ 파일/delusionist_factory_personal/mini
  python3 run_mini.py
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))  # core.py를 패키지 import 없이 직접 사용

from core import (  # noqa: E402
    DelutionistConfig,
    detect_language,
    parse_step1_1_response,
    parse_step1_response,
    split_step1_1_workers,
    split_step1_workers,
)

REQUEST_PATH = BASE_DIR / "request.json"
STAGING = BASE_DIR / "staging"
OUTPUT_DIR = BASE_DIR / "output"

CHAINS_PER_WORKER = 25
GEMINI_MODEL = "gemini-3.1-pro-preview"
GEMINI_TIMEOUT_S = 600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("delusionist-mini")


# ─── request.json 파싱 ───────────────────────────────────────────────────

def load_request(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"request.json not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))

    direction = (data.get("DIRECTION") or "").strip()
    starting = (data.get("STARTING_SENTENCE") or "").strip()
    mandatory = [w.strip() for w in (data.get("MANDATORY_WORD") or []) if w and w.strip()]
    imagery = [w.strip() for w in (data.get("PREFERRED_IMAGERY") or []) if w and w.strip()]
    mode_raw = (data.get("MODE_SELECTION") or "NUANCE").strip().upper()
    final_lang_raw = (data.get("FINAL_LANGUAGE") or "Auto").strip()

    # IDEA_COUNT 우선, 호환을 위해 PROBLEM_COUNT도 받음
    idea_count_raw = data.get("IDEA_COUNT", data.get("PROBLEM_COUNT"))

    missing = []
    if not direction:
        missing.append("DIRECTION")
    if not starting:
        missing.append("STARTING_SENTENCE")
    if not mandatory:
        missing.append("MANDATORY_WORD")
    if idea_count_raw in (None, "", 0):
        missing.append("IDEA_COUNT")
    if missing:
        raise RuntimeError(
            "request.json 필수 필드 누락/빈값: " + ", ".join(missing)
        )

    try:
        idea_count = int(idea_count_raw)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"IDEA_COUNT는 정수여야 한다: {idea_count_raw!r}") from e
    if idea_count <= 0:
        raise RuntimeError(f"IDEA_COUNT는 1 이상이어야 한다: {idea_count}")

    if mode_raw not in ("CHAOS", "NUANCE"):
        mode_raw = "NUANCE"

    final_language = detect_language(starting + " " + direction, final_lang_raw)

    return {
        "direction": direction,
        "starting": starting,
        "mandatory": mandatory,
        "imagery": imagery,
        "mode": mode_raw,
        "final_language": final_language,
        "idea_count": idea_count,
    }


# ─── gemini CLI 호출 ─────────────────────────────────────────────────────

def call_gemini(prompt: str) -> str:
    try:
        proc = subprocess.run(
            ["gemini", "--approval-mode", "plan", "-m", GEMINI_MODEL, "-p", prompt],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=GEMINI_TIMEOUT_S,
        )
        if proc.returncode != 0:
            logger.error(
                "gemini 에러 (rc=%d): %s", proc.returncode, (proc.stderr or "")[:500]
            )
            return ""
        return proc.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error("gemini 타임아웃 (%ds)", GEMINI_TIMEOUT_S)
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.error("gemini 호출 실패: %s", exc)
        return ""


# ─── Step 1 / Step 1-1 병렬 실행 ─────────────────────────────────────────

def _run_workers_parallel(workers, label: str, parser) -> list[str]:
    if not workers:
        return []
    results: dict[int, list[str]] = {}
    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        futs = {
            pool.submit(call_gemini, w.prompt_path.read_text(encoding="utf-8")): w
            for w in workers
        }
        for fut in as_completed(futs):
            w = futs[fut]
            try:
                resp = fut.result()
                lines = parser(resp)
                results[w.worker_id] = lines
                logger.info(
                    "[%s] worker %d 완료 — %d줄 (목표 %d)",
                    label, w.worker_id, len(lines), w.line_count,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("[%s] worker %d 실패: %s", label, w.worker_id, exc)
                results[w.worker_id] = []
    flat: list[str] = []
    for wid in sorted(results):
        flat.extend(results[wid])
    return flat


def step1_generate(cfg: DelutionistConfig, total_chains: int) -> list[str]:
    workers = split_step1_workers(
        cfg=cfg,
        total_chains=total_chains,
        chains_per_worker=CHAINS_PER_WORKER,
        staging_dir=STAGING,
        model="",
    )
    logger.info(
        "[Step 1] 망상 chain 생성 — %d chain × %d worker",
        total_chains, len(workers),
    )
    chains = _run_workers_parallel(workers, "Step 1", parse_step1_response)
    logger.info("[Step 1] 완료 — 총 %d chain", len(chains))
    return chains


def step1_1_filter(cfg: DelutionistConfig, chains: list[str]) -> list[str]:
    workers = split_step1_1_workers(
        cfg=cfg,
        chains=chains,
        chains_per_worker=CHAINS_PER_WORKER,
        staging_dir=STAGING,
        model="",
    )
    logger.info(
        "[Step 1-1] PPB 폐기 + 아이디어 변환 — %d chain × %d worker (목표 잔류 %d)",
        len(chains), len(workers), sum(w.line_count for w in workers),
    )
    ideas = _run_workers_parallel(workers, "Step 1-1", parse_step1_1_response)
    logger.info("[Step 1-1] 완료 — 총 %d 아이디어", len(ideas))
    return ideas


# ─── 메인 ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Delusionist Mini — Step 1 + Step 1-1만 실행")
    parser.add_argument(
        "--config", type=Path, default=REQUEST_PATH,
        help=f"request.json 경로 (기본 {REQUEST_PATH})",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="출력 파일 경로 (기본 output/ideas_<timestamp>.md)",
    )
    parser.add_argument(
        "--ideas", type=int, default=None,
        help="IDEA_COUNT 오버라이드",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="설정 파싱 결과만 출력하고 종료 (gemini 호출 없음)",
    )
    args = parser.parse_args()

    inputs = load_request(args.config)
    if args.ideas is not None:
        if args.ideas <= 0:
            logger.error("--ideas는 1 이상이어야 한다.")
            return 1
        inputs["idea_count"] = args.ideas

    logger.info("=" * 60)
    logger.info("Delusionist Mini — 설정")
    logger.info("=" * 60)
    logger.info("DIRECTION (앞 80자): %s...", inputs["direction"][:80])
    logger.info("STARTING_SENTENCE: %s", inputs["starting"][:80])
    logger.info("MANDATORY_WORD: %s", inputs["mandatory"])
    logger.info("PREFERRED_IMAGERY: %s", inputs["imagery"])
    logger.info("IDEA_COUNT: %d", inputs["idea_count"])
    logger.info("FINAL_LANGUAGE: %s", inputs["final_language"])
    logger.info("MODE_SELECTION: %s", inputs["mode"])
    logger.info("=" * 60)

    if args.dry_run:
        return 0

    cfg = DelutionistConfig(
        direction=inputs["direction"],
        starting=inputs["starting"],
        mandatory=inputs["mandatory"],
        imagery=inputs["imagery"],
        mode=inputs["mode"],
        final_language=inputs["final_language"],
    )

    idea_count = inputs["idea_count"]
    # Step 1-1이 1/5 폐기 → 4/5 잔존. idea_count를 보장하려면 5/4배 chain 필요.
    total_chains = (idea_count * 5 + 3) // 4  # ceiling

    chains = step1_generate(cfg, total_chains)
    if not chains:
        logger.error("Step 1 실패 — chain 0개")
        return 1

    ideas = step1_1_filter(cfg, chains)
    if not ideas:
        logger.error("Step 1-1 실패 — idea 0개")
        return 1

    # 디버그 보존
    STAGING.mkdir(parents=True, exist_ok=True)
    (STAGING / "step1_chains.txt").write_text(
        "\n".join(chains) + "\n", encoding="utf-8"
    )
    (STAGING / "step1_1_ideas.txt").write_text(
        "\n".join(ideas) + "\n", encoding="utf-8"
    )

    # 산출물
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        out_path = args.output
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        out_path = OUTPUT_DIR / f"ideas_{ts}.md"

    out_path.write_text("\n".join(ideas) + "\n", encoding="utf-8")

    logger.info("=" * 60)
    logger.info("완료 — %d 아이디어 (요청 %d)", len(ideas), idea_count)
    logger.info("산출물: %s", out_path)
    logger.info("=" * 60)
    if len(ideas) < idea_count:
        logger.warning(
            "요청 %d개에 미달. 재실행하거나 IDEA_COUNT를 낮춰 다시 시도.",
            idea_count,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

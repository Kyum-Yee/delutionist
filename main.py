
import os
import sys
import json
import random
import logging
import shutil
import fcntl
import shlex

# Configure logging to stderr explicitly (avoid polluting stdout for MCP)
logging.basicConfig(level=logging.INFO, format='[DELUSIONIST] %(message)s', stream=sys.stderr)


class FileLock:
    """프로세스 간 파일 잠금 (fcntl.flock 기반). 병렬 에이전트의 동시 쓰기 방지."""

    def __init__(self, lock_path):
        self.lock_path = lock_path

    def __enter__(self):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        self.f = open(self.lock_path, 'w')
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


class DelusionistFactory:
    DEFAULT_STEP1_MODE = "GEMINI_CLI"  # A-step is external by default
    # Avoid hardcoding a specific model to prevent churn; let `gemini` CLI pick its default.
    DEFAULT_GEMINI_MODEL = ""

    def __init__(self, agent_id: str = ""):
        """Workspace layout:
          - agent_id != "":  input|output|staging are scoped to <agent_id>/
          - agent_id == "":  legacy single-tenant layout (local-only fallback;
                             the MCP backend always passes an id)
        """
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.agent_id = agent_id

        if agent_id:
            self.input_dir = os.path.join(self.base_dir, 'input', 'agents', agent_id)
            self.output_dir = os.path.join(self.base_dir, 'output', 'agents', agent_id)
            self.staging_dir = os.path.join(self.base_dir, 'staging', 'agents', agent_id)
        else:
            self.input_dir = os.path.join(self.base_dir, 'input')
            self.output_dir = os.path.join(self.base_dir, 'output')
            self.staging_dir = os.path.join(self.base_dir, 'staging')

        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.staging_dir, exist_ok=True)

        self.request_path = os.path.join(self.input_dir, 'request.json')
        self.word_pool_path = None  # Will be set dynamically in run() based on request.json
        self.state_path = os.path.join(self.staging_dir, 'state.json')

        # Output files for each step
        self.section_a_path = os.path.join(self.output_dir, 'section_a_chains.txt')
        self.section_b_path = os.path.join(self.output_dir, 'section_b_refined.txt')
        self.section_c_path = os.path.join(self.output_dir, 'section_c_final.txt')

        # Lock files for concurrent access
        self.append_lock_path = os.path.join(self.staging_dir, 'append.lock')
        self.state_lock_path = os.path.join(self.staging_dir, 'state.lock')
        self.config_lock_path = os.path.join(self.staging_dir, 'config.lock')

    def _format_duration(self, seconds: int) -> str:
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        if m <= 0:
            return f"{s}s"
        return f"{m}m {s:02d}s"

    def _gemini_available(self) -> bool:
        return shutil.which("gemini") is not None

    def _resolve_language_and_pool(self, req: dict) -> tuple[str, str]:
        starting = req.get("STARTING_SENTENCE", "")
        direction = req.get("DIRECTION", "")
        final_language = req.get("FINAL_LANGUAGE", "Korean")

        # 1) Explicit preference
        final_lang_upper = str(final_language).strip().upper()
        if final_lang_upper == "KOREAN":
            is_korean = True
        elif final_lang_upper == "ENGLISH":
            is_korean = False
        else:
            # 2) Auto-detection fallback
            content_to_check = str(starting) + str(direction)
            is_korean = self._is_korean(content_to_check)

        if is_korean:
            return (os.path.join(self.base_dir, 'extracted_words.txt'), "Korean")
        return (os.path.join(self.base_dir, '100000word.txt'), "English")

    # Mode definitions — pure verb-vs-non-verb coherence axis. No examples.
    MODE_DEFINITIONS = {
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

    def _describe_mode(self, mode: str) -> str:
        return self.MODE_DEFINITIONS.get(mode.strip().upper(), self.MODE_DEFINITIONS["NUANCE"])

    def _build_step1_gemini_prompt(
        self,
        direction: str,
        starting: str,
        mandatory: list[str],
        imagery: list[str],
        language_rule: str,
        batch_start: int,
        batch_random_words: list[list[str]],
        mode: str = "NUANCE",
    ) -> str:
        # Keep prompt compact but strict about output formatting for easy parsing.
        lines = []
        lines.append("You are generating creative Korean sentences for a pipeline step called STEP 1 (CHAINING).")
        lines.append("")
        lines.append("OUTPUT FORMAT (STRICT):")
        lines.append(f"- Return exactly {len(batch_random_words)} lines.")
        lines.append("- Each line starts with a 3-digit number (e.g., 001, 002...) followed by a period and space, then the sentence.")
        lines.append("- In each sentence, wrap the random words (or their domain-adapted variants) in markdown bold (**word**). At least 3 bold words per line.")
        lines.append("- End each sentence with a parenthetical annotation: what collision emerged, what direction it could go. e.g., (충돌: 빙하+요리 → 느린 해동 조리법 가능성)")
        lines.append("- No titles, no explanations, no extra blank lines.")
        lines.append("")
        lines.append("MODE:")
        lines.append(f"- {self._describe_mode(mode)}")
        lines.append("")
        lines.append("CONSTRAINTS:")
        if mandatory:
            lines.append(f"- Every line MUST include ALL mandatory words exactly as written: {', '.join(mandatory)}")
        if language_rule:
            lines.append(f"- LANGUAGE_RULE: {language_rule}")
        if imagery:
            lines.append(f"- Prefer imagery motifs: {', '.join(imagery)}")
        lines.append("- The sentences should feel like a surreal collision (bold, unexpected connections); naturalness is governed by the MODE rule above.")
        lines.append("- Random words are for context pollution: you may replace them with context-fitting variants if needed, but keep the 'collision' spirit.")
        lines.append("")
        lines.append("CONTEXT:")
        if direction:
            lines.append("DIRECTION:")
            lines.append(direction.strip())
            lines.append("")
        if starting:
            lines.append("STARTING_SENTENCE (seed tone/energy, do not copy verbatim if awkward):")
            lines.append(starting.strip())
            lines.append("")
        lines.append("RANDOM WORDS PER LINE:")
        for idx, words in enumerate(batch_random_words, start=batch_start):
            joined = ", ".join(words)
            lines.append(f"- Line {idx}: {joined}")
        lines.append("")
        lines.append("Now produce the lines.")
        return "\n".join(lines).strip() + "\n"

    def prepare_step1_gemini_prompt(self, batch_size: int = 30) -> dict:
        """
        Prepares the STEP 1 Gemini prompt (external execution) and writes it to staging.
        Returns metadata including the prompt path and recommended CLI command.
        """
        req = self.load_request()
        if not req:
            raise RuntimeError("request.json not found")

        chains_target = req.get("CHAINS_COUNT", 100)
        direction = req.get("DIRECTION", "")
        starting = req.get("STARTING_SENTENCE", "")
        mandatory = req.get("MANDATORY_WORD", [])
        imagery = req.get("PREFERRED_IMAGERY", [])
        language_rule = req.get("LANGUAGE_RULE", "NO_3_CONSECUTIVE_FOREIGN_WORDS")
        mode = req.get("MODE_SELECTION", "NUANCE")

        chains_done = self.count_lines(self.section_a_path)
        remaining = max(0, int(chains_target) - int(chains_done))
        current_batch = min(int(batch_size), remaining)
        batch_start = chains_done + 1
        batch_end = chains_done + current_batch
        total_batches = (int(chains_target) + int(batch_size) - 1) // int(batch_size)
        batch_index = (int(chains_done) // int(batch_size)) + 1

        # Determine word pool based on request
        self.word_pool_path, detected_lang = self._resolve_language_and_pool(req)

        batch_random_words: list[list[str]] = []
        for _ in range(current_batch):
            batch_random_words.append(self.get_random_words_from_file(self.word_pool_path, 3))

        prompt = self._build_step1_gemini_prompt(
            direction=direction,
            starting=starting,
            mandatory=mandatory,
            imagery=imagery,
            language_rule=language_rule,
            batch_start=batch_start,
            batch_random_words=batch_random_words,
            mode=mode,
        )

        prompt_path = os.path.join(self.staging_dir, "step1_gemini_prompt.txt")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)

        model = os.getenv("DELUSIONIST_GEMINI_MODEL", self.DEFAULT_GEMINI_MODEL).strip()
        if model:
            cmd = f"gemini --output-format json --model {shlex.quote(model)} \"$(cat {shlex.quote(prompt_path)})\""
            model_label = model
        else:
            # If Gemini CLI updates its default (e.g. newer preview), this stays future-proof.
            cmd = f"gemini --output-format json \"$(cat {shlex.quote(prompt_path)})\""
            model_label = "(gemini CLI default)"

        # ETA: best-effort heuristic (configurable via env for your machine/model).
        # Defaults are intentionally conservative to avoid premature "it hung" assumptions.
        # Set:
        # - DELUSIONIST_STEP1_ETA_OVERHEAD_S (default 20)
        # - DELUSIONIST_STEP1_ETA_S_PER_LINE (default 1.2)
        overhead_s = float(os.getenv("DELUSIONIST_STEP1_ETA_OVERHEAD_S", "20").strip() or "20")
        per_line_s = float(os.getenv("DELUSIONIST_STEP1_ETA_S_PER_LINE", "1.2").strip() or "1.2")
        eta_s = int(overhead_s + (current_batch * per_line_s))
        # Give a range (x0.7 ~ x1.6) since network/auth variance is real.
        eta_low = int(eta_s * 0.7)
        eta_high = int(eta_s * 1.6)
        eta_text = f"~{self._format_duration(eta_low)} to {self._format_duration(eta_high)}"

        return {
            "prompt_path": prompt_path,
            "cmd": cmd,
            "chains_done": chains_done,
            "chains_target": chains_target,
            "current_batch": current_batch,
            "batch_start": batch_start,
            "batch_end": batch_end,
            "batch_index": batch_index,
            "total_batches": total_batches,
            "eta_text": eta_text,
            "detected_lang": detected_lang,
            "word_pool": os.path.basename(self.word_pool_path),
            "model": model_label,
        }

    def load_request(self):
        if not os.path.exists(self.request_path):
            return None
        with open(self.request_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    # Word pool line counts (pre-calculated constants to avoid loading full file)
    WORD_POOL_LINE_COUNTS = {
        "extracted_words.txt": 917273,  # Korean word pool
        "100000word.txt": 466551,       # English word pool
    }

    def get_line_count(self, filepath):
        """Get total line count using constants."""
        filename = os.path.basename(filepath)
        return self.WORD_POOL_LINE_COUNTS.get(filename, 10000)

    def get_random_words_from_file(self, filepath, count=3):
        """
        Efficient random word selection using linecache.
        Avoids loading 24MB+ text files into memory.
        """
        import linecache
        
        total_lines = self.get_line_count(filepath)
        if total_lines == 0:
            return []
        
        # Pick random line numbers (1-indexed for linecache)
        target_lines = random.sample(range(1, total_lines + 1), min(count, total_lines))
        
        words = []
        for line_num in target_lines:
            line = linecache.getline(filepath, line_num)
            stripped = line.strip()
            if stripped:
                words.append(stripped)
        
        return words

    def _analyze_vocab_level(self, direction):
        """
        DIRECTION 텍스트를 AI에게 전달하여 적절한 어휘 수준 판단을 유도.
        (키워드 기반 자동 분석 대신 AI가 맥락을 파악하도록 함)
        """
        # AI가 직접 판단하도록 가이드만 제공
        return f"DIRECTION 분석 후 적절한 어휘 수준 판단: '{direction[:50]}...'"

    def _is_korean(self, text):
        """텍스트에 한국어가 포함되어 있는지 확인"""
        if not text:
            return False
        # 한글 유니코드 범위 확인 (가-힣)
        import re
        return bool(re.search("[가-힣]", text))

    def load_state(self):
        with FileLock(self.state_lock_path):
            if not os.path.exists(self.state_path):
                return {"current_step": 1}
            try:
                with open(self.state_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {"current_step": 1}

    def save_state(self, state):
        with FileLock(self.state_lock_path):
            with open(self.state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)

    def locked_append(self, filepath, content):
        """파일 잠금 기반 안전한 append. 병렬 에이전트 동시 쓰기 방지."""
        with FileLock(self.append_lock_path):
            with open(filepath, 'a', encoding='utf-8') as f:
                f.write(content.strip() + '\n')

    def count_lines(self, filepath):
        if not os.path.exists(filepath):
            return 0
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                # Ignore whitespace-only lines (including indented blank lines).
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def prepare_parallel_batches(self, worker_count=None, batch_size=None):
        """
        Step 1 병렬 실행용 배치 준비.

        두 가지 모드:
        - worker_count: 워커 수 지정 → 남은 줄을 균등 분할
        - batch_size: 워커당 줄 수 지정 → 워커 수 자동 계산 (ceil(remaining / batch_size))
        둘 다 미지정 시 batch_size=25 기본값.

        Returns: list[dict] — 각 워커의 {worker_id, line_count, random_words, context}
        """
        req = self.load_request()
        if not req:
            raise RuntimeError("request.json not found")

        chains_target = req.get("CHAINS_COUNT", 100)
        chains_done = self.count_lines(self.section_a_path)
        remaining = max(0, chains_target - chains_done)

        if remaining == 0:
            return []

        # batch_size 우선: 워커당 줄 수로 워커 수 역산
        if batch_size is not None and batch_size >= 1:
            worker_count = -(-remaining // batch_size)  # ceil(remaining / batch_size)
        elif worker_count is not None and worker_count >= 1:
            pass  # worker_count 그대로 사용
        else:
            # 둘 다 미지정 → batch_size=25 기본값
            batch_size = 25
            worker_count = -(-remaining // batch_size)

        self.word_pool_path, detected_lang = self._resolve_language_and_pool(req)

        # 전체 랜덤 단어 미리 생성
        all_random_words = [
            self.get_random_words_from_file(self.word_pool_path, 3)
            for _ in range(remaining)
        ]

        # worker_count개로 균등 분할
        chunk_size = -(-remaining // worker_count)  # ceiling division
        context = {
            "direction": req.get("DIRECTION", ""),
            "starting_sentence": req.get("STARTING_SENTENCE", ""),
            "mandatory_words": req.get("MANDATORY_WORD", []),
            "preferred_imagery": req.get("PREFERRED_IMAGERY", []),
            "language_rule": req.get("LANGUAGE_RULE", "NO_3_CONSECUTIVE_FOREIGN_WORDS"),
            "mode": req.get("MODE_SELECTION", "CHAOS"),
        }

        batches = []
        for i in range(worker_count):
            start_idx = i * chunk_size
            end_idx = min(start_idx + chunk_size, remaining)
            if start_idx >= remaining:
                break
            batches.append({
                "worker_id": i + 1,
                "line_count": end_idx - start_idx,
                "random_words": all_random_words[start_idx:end_idx],
                "context": context,
            })

        return batches

    def prepare_parallel_gemini_workers(
        self,
        worker_count: int | None = None,
        batch_size: int | None = None,
    ) -> list[dict]:
        """
        Step 1 병렬 Gemini CLI 워커 준비.

        prepare_parallel_batches()와 동일한 분할 로직이지만,
        각 워커에 대해 staging/worker_{id}_prompt.txt를 생성하고
        실행할 gemini 명령어(cmd)를 함께 반환한다.

        Operator(메인 에이전트)가 run_command로 gemini를 직접 병렬 실행하고
        응답을 append_result로 올리면 sub-agent 토큰이 0이 된다.

        Returns:
            list[dict] — 워커별 {
                worker_id,
                line_count,
                prompt_path,   # staging/worker_{id}_prompt.txt
                cmd,           # 실행할 gemini 명령어 (문자열)
                batch_start,   # 이 워커가 담당하는 시작 줄 번호
                batch_end,     # 이 워커가 담당하는 끝 줄 번호
            }
        """
        batches = self.prepare_parallel_batches(
            worker_count=worker_count,
            batch_size=batch_size,
        )
        if not batches:
            return []

        req = self.load_request()
        if not req:
            raise RuntimeError("request.json not found")

        direction = req.get("DIRECTION", "")
        starting = req.get("STARTING_SENTENCE", "")
        mandatory = req.get("MANDATORY_WORD", [])
        imagery = req.get("PREFERRED_IMAGERY", [])
        language_rule = req.get("LANGUAGE_RULE", "NO_3_CONSECUTIVE_FOREIGN_WORDS")
        mode = req.get("MODE_SELECTION", "NUANCE")
        model = os.getenv("DELUSIONIST_GEMINI_MODEL", self.DEFAULT_GEMINI_MODEL).strip()

        # chains_done 기준으로 전역 줄 번호 계산
        chains_done = self.count_lines(self.section_a_path)
        workers_out = []
        running_offset = 0  # 이 워커 이전까지 생성된 줄 수의 합

        for batch in batches:
            wid = batch["worker_id"]
            wcount = batch["line_count"]
            batch_start = chains_done + running_offset + 1
            batch_end = chains_done + running_offset + wcount
            random_words = batch["random_words"]  # list[list[str]], 길이 == wcount

            prompt = self._build_step1_gemini_prompt(
                direction=direction,
                starting=starting,
                mandatory=mandatory,
                imagery=imagery,
                language_rule=language_rule,
                batch_start=batch_start,
                batch_random_words=random_words,
                mode=mode,
            )

            prompt_path = os.path.join(
                self.staging_dir, f"worker_{wid}_prompt.txt"
            )
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)

            if model:
                cmd = (
                    f'gemini --output-format json --model {shlex.quote(model)} '
                    f'"$(cat {shlex.quote(prompt_path)})"'
                )
            else:
                cmd = f'gemini --output-format json "$(cat {shlex.quote(prompt_path)})"'

            workers_out.append(
                {
                    "worker_id": wid,
                    "line_count": wcount,
                    "prompt_path": prompt_path,
                    "cmd": cmd,
                    "batch_start": batch_start,
                    "batch_end": batch_end,
                }
            )
            running_offset += wcount

        return workers_out

    def run(self):
        logging.info("Initializing Delusionist Factory Engine...")
        
        # 1. Load Request
        req = self.load_request()
        if not req:
            logging.error("request.json not found!")
            return
        
        starting = req.get("STARTING_SENTENCE", "")
        mandatory = req.get("MANDATORY_WORD", [])
        imagery = req.get("PREFERRED_IMAGERY", [])
        chains_target = req.get("CHAINS_COUNT", 100)
        mode = req.get("MODE_SELECTION", "CHAOS").strip().upper()
        selection_b_count = req.get("SELECTION_B_COUNT", 10)  # Step 2에서 추출할 문장 수
        refining_count = req.get("REFINING_COUNT", 1)  # Step 3 최종 출력 수
        direction = req.get("DIRECTION", "")
        final_language = req.get("FINAL_LANGUAGE", "Korean")  # Step 3 출력 언어
        language_rule = req.get("LANGUAGE_RULE", "NO_3_CONSECUTIVE_FOREIGN_WORDS")
        # Step 1 executor selection:
        # - request.json: STEP1_EXECUTOR = "GEMINI_CLI" | "SELF"
        # - env override: DELUSIONIST_STEP1
        step1_mode = (req.get("STEP1_EXECUTOR") or os.getenv("DELUSIONIST_STEP1") or self.DEFAULT_STEP1_MODE).strip().upper()
        if step1_mode not in ("GEMINI_CLI", "SELF"):
            step1_mode = self.DEFAULT_STEP1_MODE
        
        self.word_pool_path, detected_lang = self._resolve_language_and_pool(req)

        # word_pool = self.load_word_pool() # REMOVED: Memory inefficiency
        state = self.load_state()
        mode_definition = self._describe_mode(mode)

        logging.info(f"[CONFIG] Mode: {mode} | Chains: {chains_target}")
        logging.info(f"[CONFIG] Selection B: {selection_b_count} | Final Output: {refining_count}")
        logging.info(f"[CONFIG] Detected Language: {detected_lang} -> Pool: {os.path.basename(self.word_pool_path)}")
        logging.info(f"[CONFIG] Mode definition: {mode_definition}")
        
        # ========== STEP 1: Chaining CoT ==========
        if state["current_step"] == 1:
            chains_done = self.count_lines(self.section_a_path)
            BATCH_SIZE = req.get("STEP1_BATCH_SIZE", 25)
            
            if chains_done < chains_target:
                # Calculate batch info
                remaining = chains_target - chains_done
                current_batch = min(BATCH_SIZE, remaining)
                batch_start = chains_done + 1
                batch_end = chains_done + current_batch
                
                # Generate random words for each chain in this batch
                batch_random_words = []
                for i in range(current_batch):
                    batch_random_words.append(self.get_random_words_from_file(self.word_pool_path, 3))
                
                logging.info(f"[STEP 1] Chaining Progress: {chains_done}/{chains_target}")

                if step1_mode == "GEMINI_CLI":
                    info = self.prepare_step1_gemini_prompt(batch_size=BATCH_SIZE)

                    print("\n" + "="*70)
                    print(f"  [STEP 1: EXTERNAL (GEMINI CLI)] - Batch #{info['batch_start']}~{info['batch_end']} / {chains_target}")
                    print("="*70)
                    print("  NOTE: MCP/Agent does NOT generate STEP 1. Run Gemini CLI and append results.")
                    print(f"  - Batch: {info['batch_index']}/{info['total_batches']} | ETA: {info['eta_text']}")
                    print(f"  - Prompt saved to: {info['prompt_path']}")
                    print(f"  - Recommended model: {info['model']}")
                    print("  - Run:")
                    print(f"      {info['cmd']}")
                    print("  - Then append the returned lines to:")
                    print(f"      {self.section_a_path}")
                    print("  - After you have enough lines, re-run and it will advance to STEP 2.")
                    print("="*70 + "\n")
                    return
                
                print("\n" + "="*70)
                print(f"  [STEP 1: CHAINING CoT] - Batch #{batch_start}~{batch_end} / {chains_target}")
                print("="*70)
                print("  ")
                print("  ## 💡 Core Concept: Stochastic Context Pollution")
                print("  - An LLM cannot surprise itself. External randomness breaks its probability curve.")
                print("  - A clean context yields cliché; a polluted context forces unexpected connections.")
                print("  ")
                print("  ## 📋 Configuration & Context")
                print(f"  - 🎯 DIRECTION: \"{direction}\"")
                print(f"  - 🌱 STARTING_SENTENCE: \"{starting}\"")
                print(f"  - 🔑 MANDATORY_WORD: {', '.join(mandatory)}")
                print(f"  - 🎨 PREFERRED_IMAGERY: {', '.join(imagery)}")
                print(f"  - ⚙️ MODE: {mode}")
                print(f"       └ {mode_definition}")
                print("  ")
                print("  ## 🎲 Random Word Injection (Context Pollution)")
                print(f"  Random words for this batch ({current_batch} lines):")
                print("  " + "-"*66)
                for idx, words in enumerate(batch_random_words, start=batch_start):
                    print(f"     [{idx:03d}] {', '.join(words)}")
                print("  " + "-"*66)
                print("  ")
                print("  ## 🚀 Agent Action Required")
                print("  ")
                print(f"  1. Read DIRECTION carefully. It is the destination every line aims at.")
                print(f"  2. Collide STARTING_SENTENCE with the random words above to produce 'delusional sentences' — bold, unexpected combinations.")
                print(f"  3. [MODE — applies to every line] {mode_definition}")
                print(f"     → CHAOS: the verb is NOT required to agree semantically with its subject/object. Both coherence and incoherence are allowed; neither is forced.")
                print(f"     → NUANCE: the verb MUST agree with its subject/object. Incoherence, if present, is allowed only among non-verbal components (modifiers, settings, co-occurring entities). Fully coherent sentences also pass.")
                print(f"  4. [Authority] If you need more variation, raise CHAINS_COUNT aggressively via `update_request_config` (e.g., 50, 100, 200, 300).")
                print(f"  5. [Mandatory] Include '{', '.join(mandatory)}' in every line. Obey {language_rule}.")
                print(f"  6. [CONTEXT RULE] Naturalness of context comes first. If a random word feels alien, don't keep it verbatim — swap it for a context-fitting variant.")
                print(f"  7. Esoteric jargon (medicine, engineering, chemistry, linguistics, art, etc.) is allowed ONLY when it actually fits the context. Otherwise, replace with a plain term.")
                print("  ")
                print(f"  8. [Output format] Prefix each sentence with a 3-digit number (001, 002, ...).")
                print(f"     → For each line, use the 3 random words assigned to its line number 1:1. ([001]'s 3 words → line 001 only; [002]'s 3 words → line 002 only; no overlap across lines.)")
                print(f"     → Wrap each used random word (or its domain-adapted variant) in markdown bold (**word**). At least 3 bolded words per line.")
                print(f"     → Example: [001] = glacier, voyage, crack →")
                print(f"        001. The **crack** in texture lets a single plate **voyage** through itself, melting like a slow **glacier**. (Collision: glacier + cooking → slow-thaw cooking technique)")
                print(f"  9. [Annotation required] At the end of each sentence, in parentheses, write a one-line note:")
                print(f"     → What collision happened in this line, and which direction it could grow toward.")
                print(f"     → Step 2 may optionally use this note to expand the idea.")
                print("  ")
                print(f"  👉 Goal: {current_batch} bold, unexpected sentences aimed at DIRECTION (\"{direction[:30]}...\").")
                print(f"  👉 Action: append the result to `{self.section_a_path}` exactly.")
                print("  ")
                print("="*70 + "\n")
                return
            
            else:
                # Audit: Verify mandatory words in all chains
                logging.info(f"[STEP 1] ✅ Chaining Complete! ({chains_done} chains)")
                
                # Move to Step 2
                state["current_step"] = 2
                self.save_state(state)
                logging.info("[STATE] Advancing to STEP 2...")
        
        # ========== STEP 2: Refining CoT (문장 추출 - Batch Mode) ==========
        if state["current_step"] == 2:
            refined_done = self.count_lines(self.section_b_path)
            BATCH_SIZE = 10
            
            if refined_done < selection_b_count:
                remaining = selection_b_count - refined_done
                current_batch = min(BATCH_SIZE, remaining)
                batch_start = refined_done + 1
                batch_end = refined_done + current_batch
                
                logging.info(f"[STEP 2] Selection B Progress: {refined_done}/{selection_b_count}")
                
                print("\n" + "="*70)
                print(f"  [STEP 2: REFINING CoT] - Selection B #{batch_start}~{batch_end} / {selection_b_count}")
                print("="*70)
                print("  ")
                print("  ## 💡 Core Concept: Collision → Concrete Mechanism (preserve depth, strip abstraction)")
                print("  - Preserve the 'depth' the Step 1 collisions produced — the tension, the gap, the accumulated weight of meaning across two domains.")
                print("  - Strip the 'abstractness' from the result — vague generalizations and ungraspable phrasing must go.")
                print("  - Good refinement: the result lands on something concrete you can hold, yet the depth of the collision is still preserved in it.")
                print("  - Bad refinement 1: it escapes into abstract phrases like 'the essence of X' or 'the aesthetics of Y' and the depth evaporates.")
                print("  - Bad refinement 2: the depth gets flattened into a one-liner — \"basically, an idea about X.\"")
                print("  ")
                print("  ## 📋 Refinement Context")
                print(f"  - 🎯 DIRECTION: \"{direction}\"")
                print(f"  - 🖼 PREFERRED_IMAGERY: {', '.join(imagery)}")
                print(f"  - 🔍 Source File: {self.section_a_path} (STEP 1 Output)")
                print("  ")
                print("  ## 🚀 Agent Action Required")
                print("  ")
                print(f"  0. [Use Step 1 annotations] The parenthetical notes at the end of Step 1 lines are optional but useful — treat them as hints for new angles.")
                print(f"  1. [Identify collisions] Find combinations of ideas/notions in Step 1 that have not been put together before.")
                print(f"     → Mere restatement of an existing idea is rejected.")
                print(f"     → Only keep crossings of two or more distinct domains.")
                print(f"  2. [Process Step 1 material — combine and edit] Do not copy collisions verbatim.")
                print(f"     → If a Step 1 line had real potential but the model didn't use it well, edit it directly.")
                print(f"     → If two or more ideas only become complete when fused, fuse them. If anything is still missing afterward, edit further.")
                print(f"     → Goal of processing: preserve depth, strip abstraction (see #4).")
                print(f"  3. [PRUNING — do NOT pre-expand candidates n-fold]")
                print(f"     → Pre-expanding the candidate pool to several times the target only to cut it down is a waste. Don't do it.")
                print(f"     → Self-censorship rule: \"ingenious but not absurd\".")
                print(f"     → Append only what survives in this batch. If you're on the fence about keeping one — drop it. Being on the fence already means it failed self-censorship once.")
                print(f"     → If this batch comes up short, end short. The next batch picks up the slack naturally.")
                print(f"  4. [Preserve depth, strip abstraction]")
                print(f"     → Depth: the tension, gap, and accumulated meaning the collision produced → keep.")
                print(f"     → Abstractness: vague generalizations, ungraspable phrasing → remove.")
                print(f"     → The result lands on something concrete, yet the depth of the collision that got you there is still embedded in it.")
                print(f"     → Reductions to \"basically, an idea about X\" or escapes into \"the essence of\" / \"the aesthetics of\" are rejected.")
                print(f"  5. [Naming — original + grounded by default. Retreat only when truly impossible.]")
                print(f"     → Name the collision and its mechanism. Attempt naming actively.")
                print(f"     → Conditions for a good name:")
                print(f"        ❌ Self-referential / arbitrary — a name that points only to its inspiration source, telling the reader nothing about its mechanism or domain.")
                print(f"        ✅ Original + grounded — a fresh compound that still works inside the existing vocabulary system. The name itself should reveal \"principle + key qualifier.\" If a clean acronym fits, attach it.")
                print(f"     → Only when no good name actually emerges, fall back to a plain functional descriptor (\"this recipe\", \"this structure\", \"this pattern\"). Retreat-as-an-excuse is forbidden.")
                print(f"  6. [Make it real] Describe what the (named or plainly-referred) collision actually is, in concrete terms.")
                print(f"     → If it reduces to a single existing notion, it's rejected.")
                print(f"     → It must contain at least one action (verb) or event (situation).")
                print(f"  7. [Validation] The bar is a veteran reader saying \"haven't seen this — but it makes sense.\"")
                print(f"     → \"Oh, I know that\" → fail. \"This is new and yet it tracks\" → pass.")
                print(f"  7-1. [Disqualifier] Could a domain expert come up with this idea in 10 minutes without random-word injection?")
                print(f"     → If yes, reject. The whole point of Stochastic Context Pollution is to land outside the expert's reach.")
                print(f"  8. [PPB detector] If you see any of these, delete or replace with concrete content:")
                print(f"     → Name-dropping (e.g., decorative lists like 'the thought of A, the aesthetics of B').")
                print(f"     → Substanceless metaphor chains (a metaphor explaining a metaphor explaining a metaphor).")
                print(f"     → Excessive flourish (three-deep nested possessives like 'the X of the Y of the Z').")
                print(f"  9. Verify that the mandatory words ({', '.join(mandatory)}) sit naturally in the context.")
                print(f"  10. [CONTEXT RULE] If a random word feels alien, don't keep it verbatim — swap for a context-fitting variant.")
                print(f"  11. [JARGON BAN] Esoteric jargon only when it fits. Otherwise, plain language.")
                print(f"  12. [Annotation required] End each refined line with a one-line parenthetical note:")
                print(f"     → What was striking, what material/structure it could become in Step 3, what direction it could expand.")
                print(f"     → e.g.: \"(works in Step 3 as a 'reduced execution' option for activity design; the immunology analogy is sharp.)\"")
                print("  ")
                print(f"  👉 Goal: {selection_b_count} entries final. This batch tries {current_batch}, but no forced filling — only what survives.")
                print(f"  👉 Action: append the result to `{self.section_b_path}` exactly.")
                print("  ")
                print("="*70 + "\n")
                return
            
            else:
                logging.info(f"[STEP 2] ✅ Refining Complete! ({refined_done} sentences)")
                state["current_step"] = 3
                self.save_state(state)
                logging.info("[STATE] Advancing to STEP 3...")
        
        # ========== STEP 3: Final CoT (최종 번역 - Batch Mode) ==========
        if state["current_step"] == 3:
            final_done = self.count_lines(self.section_c_path)
            BATCH_SIZE = 5

            if not state.get("step3_finalized", False):
                logging.info(f"[STEP 3] Final Progress: {final_done} lines appended (target: {refining_count} entries, not finalized)")
                current_batch = min(BATCH_SIZE, refining_count)  # advisory batch size
                
                # 어휘 수준 분석
                vocab_hint = self._analyze_vocab_level(direction)
                
                print("\n" + "="*70)
                print(f"  [STEP 3: FINAL CoT] - {final_done} lines appended / {refining_count} entries target")
                print("="*70)
                print("  ")
                print("  ## 💡 Core Concept: Objectification — speak in the DIRECTION author's voice")
                print("  - Fuse the Step 2 material, but strip out almost all of your own characteristic phrasing.")
                print("  - The final result must match the vocabulary, tone, and expertise level of whoever wrote DIRECTION.")
                print("  ")
                print("  ## 📋 Final Context")
                print(f"  - 🎯 DIRECTION: \"{direction}\"")
                print(f"  - 🗣 FINAL_LANGUAGE: {final_language}")
                print(f"  - 📊 Analysis: {vocab_hint}")
                print("  ")
                print("  ## 🚀 Agent Action Required")
                print("  ")
                print(f"  0. [Use Step 2 annotations] The parenthetical notes at the end of Step 2 lines are optional hints for which material/structure to lean on.")
                print(f"  0-1. [Pick the Main Idea — required before writing] Read the full Step 2 output and pick exactly ONE idea to be the root of the final piece.")
                print(f"     → This Main Idea must take up at least 30% of the final piece's volume and depth.")
                print(f"     → Criterion: the rest of the ideas should fold into or extend from this Main Idea.")
                print(f"     → Don't pick the 'flashiest' one — pick the one most directly tied to DIRECTION and with the highest extension potential.")
                print(f"     → Once picked, place the Main Idea explicitly in the first paragraph (or right after the title), and let everything that follows revolve around it.")
                print(f"  1. Build the final piece from the Step 2 output ({self.section_b_path}).")
                print(f"  2. [Match the language level] Analyze DIRECTION's tone, vocabulary, and expertise level, and write to that level.")
                print(f"     → If DIRECTION is technical, keep the technical terms (the AI running the MCP can explain them to the user).")
                print(f"     → If DIRECTION is everyday, switch to plain language.")
                print(f"  3. [Limit your own voice] In each final piece, your 'own original phrasing' is capped at 1 title + 1 key term.")
                print(f"     → [Restraint on naming] Even that 1 only earns a name if BOTH conditions hold: (a) the concept is essentially one-of-a-kind in the real world, AND (b) compressing it into a single word produces semantic/economic gain across two or more later references. Otherwise, use a plain phrase (\"this method\", \"this structure\").")
                print(f"     → If a plain phrase already lets the DIRECTION author go \"oh, that's just the X way,\" prefer that.")
                print(f"     → The reader should feel \"this came out of the direction I wrote.\"")
                print(f"  4. [Objectification rule] Cut subjective gushing and flourish.")
                print(f"     → Ban abstract definition-statements like \"this is the aesthetics of X\" or \"this is the language of Y.\" Use a condition-result structure instead: \"if you do X, Y happens.\"")
                print(f"     → Every paragraph must summarize in one line as \"what does this tell me to do?\"")
                print(f"  5. [Final PPB sweep] Delete or replace any of:")
                print(f"     → Name-dropping of authorities/movements without a concrete, self-verifiable, scientifically tight explanation.")
                print(f"     → Setups with no concrete action, event, or stake.")
                print(f"     → Excessive flourish; metaphor cycles where one metaphor explains another.")
                print(f"  6. [Usability] The result must be ready for the DIRECTION author to use immediately. Not a cipher to decode — readable and directly actionable.")
                print(f"  7. [Operational test] For each idea, run at least one concrete example/scenario to confirm it actually works.")
                print(f"     → For cooking, walk the recipe step by step. For literature, write a short demo paragraph using that structure. For business, run a customer scenario.")
                print(f"     → If the simulation breaks logically, physically, or structurally, fix or replace the idea.")
                print(f"     → Include the simulation result itself in the final piece so the reader can see how the mechanism actually works.")
                print(f"  8. [Beat the expectations] Read the [expectations] block inside DIRECTION and break through that ceiling.")
                print(f"     → The reaction you're aiming for is \"didn't see this coming,\" not \"this is fine.\"")
                print(f"     → If the bar is expert-level, reach a density an expert would actually share with a colleague. If the bar is age-5, make something a 5-year-old would scream with joy over.")
                print(f"  9. [Vertical depth] Dig only inside the frame DIRECTION sets. Do not wander outside it.")
                print(f"     → If the request is 'an RP character sheet,' make the best RP character sheet in the world — don't suddenly pivot to a novel or a paper.")
                print(f"     → If the request is 'age-5,' don't smuggle in age-20 material. Compete on density and precision inside the frame, not by changing frames.")
                print(f"  10. Produce {refining_count} final piece(s).")
                print("  ")
                print(f"  👉 Goal: {refining_count} self-contained pieces that fulfill DIRECTION.")
                print(f"  👉 Action: append the result to `{self.section_c_path}` exactly.")
                print(f"  👉 Completion signal: after all pieces are appended, on the final append_result call, pass finalize=true.")
                print(f"     → Step 3 only completes when finalize=true is sent.")
                print(f"     → You may split the append into multiple calls. Only the very last call needs finalize=true.")
                print("  ")
                print("="*70 + "\n")
                return
            
            else:
                logging.info(f"[STEP 3] ✅ Final Complete! (finalized, {final_done} lines)")
                logging.info("")
                logging.info("="*50)
                logging.info("  🎉 DELUSIONIST FACTORY - ALL STEPS COMPLETE!")
                logging.info("="*50)
                logging.info(f"  Section A (Chains): {self.section_a_path}")
                logging.info(f"  Section B (Refined): {self.section_b_path}")
                logging.info(f"  Section C (Final): {self.section_c_path}")
                logging.info("="*50)


if __name__ == "__main__":
    agent_id = os.environ.get("DELUSIONIST_AGENT_ID", "").strip()
    factory = DelusionistFactory(agent_id=agent_id)
    factory.run()

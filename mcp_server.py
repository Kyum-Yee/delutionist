#!/usr/bin/env python3
"""
Delusionist Factory MCP Server (multi-agent isolated workspaces).

Each agent must call register_agent() to obtain an `agent_id`, then pass that
id to every subsequent tool call. The server keeps each agent's workspace
fully isolated:

    output/agents/<id>/         (section_a/b/c)
    staging/agents/<id>/        (state.json, locks, prompts)
    input/agents/<id>/          (request.json)
    mini/staging/agents/<id>/   (mini Step 1 / 1-1 worker prompts)

Sliding 24h TTL — every successful tool call extends the agent's expiry.
Expired agents are swept lazily on each register_agent call.
"""

import os
import sys
import json
import random
import asyncio
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from main import DelusionistFactory, FileLock
from agents import AgentRegistry


# ── Initialization ────────────────────────────────────────────────────────

server = Server("delusionist-factory")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
registry = AgentRegistry(Path(BASE_DIR))

# Word pool line counts (pre-calculated constants)
WORD_POOL_LINE_COUNTS = {
    "extracted_words.txt": 917273,  # Korean word pool
    "100000word.txt": 466551,       # English word pool
}


def get_word_pool_path(factory_instance: DelusionistFactory, is_korean: bool) -> str:
    if is_korean:
        return os.path.join(factory_instance.base_dir, 'extracted_words.txt')
    return os.path.join(factory_instance.base_dir, '100000word.txt')


def get_line_count(filepath: str) -> int:
    return WORD_POOL_LINE_COUNTS.get(os.path.basename(filepath), 10000)


def get_random_words_from_file(filepath: str, count: int = 3) -> list[str]:
    """Random word sampling via linecache (no full-file load)."""
    import linecache
    total_lines = get_line_count(filepath)
    if total_lines == 0:
        return []
    target_lines = random.sample(range(1, total_lines + 1), min(count, total_lines))
    words: list[str] = []
    for line_num in target_lines:
        line = linecache.getline(filepath, line_num)
        stripped = line.strip()
        if stripped:
            words.append(stripped)
    return words


# ── Agent helpers ─────────────────────────────────────────────────────────

def _resolve_agent(arguments: dict[str, Any]):
    """Validate agent_id, slide its expiry, return a scoped factory.

    Returns (factory, None) on success, (None, error_response) on failure.
    """
    aid = str(arguments.get("agent_id") or "").strip()
    if not aid:
        return None, [TextContent(
            type="text",
            text="ERROR: 'agent_id' is required. Call register_agent first to obtain one."
        )]
    if not registry.touch(aid):
        return None, [TextContent(
            type="text",
            text=f"ERROR: agent_id '{aid}' is unknown or expired. Call register_agent again."
        )]
    return DelusionistFactory(agent_id=aid), None


def _mini_staging_dir(agent_id: str) -> Path:
    return Path(BASE_DIR) / "mini" / "staging" / "agents" / agent_id


def _read_skill(skill_dir_name: str) -> list[TextContent]:
    """Bundle a skill directory into a JSON payload the client can dump to disk."""
    skill_dir = Path(BASE_DIR) / "skills" / skill_dir_name
    if not skill_dir.exists() or not skill_dir.is_dir():
        return [TextContent(
            type="text",
            text=f"ERROR: skill '{skill_dir_name}' not found at {skill_dir}",
        )]
    files: list[dict[str, str]] = []
    for fp in sorted(skill_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(skill_dir).as_posix()
        try:
            content = fp.read_text(encoding="utf-8")
            files.append({"path": rel, "content": content})
        except Exception as e:
            files.append({"path": rel, "error": str(e)})
    payload = {
        "skill_name": skill_dir_name,
        "base_path": f"~/.claude/skills/{skill_dir_name}",
        "files": files,
        "instruction": (
            f"Save each file at <base_path>/<path> (i.e. ~/.claude/skills/{skill_dir_name}/<path>). "
            "Create the directory first if it does not exist (mkdir -p). "
            "After saving, restart Claude Code (or reload skills) to make it visible."
        ),
    }
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


# ── Step instructions builder (per-agent) ─────────────────────────────────

def get_step_instructions(step: int, factory_instance: DelusionistFactory) -> str:
    """Generate the next-step task block for the agent's factory."""
    req = factory_instance.load_request()
    if not req:
        return "ERROR: request.json not found! Use update_request_config to seed one."

    state = factory_instance.load_state()

    starting = req.get("STARTING_SENTENCE", "")
    mandatory = req.get("MANDATORY_WORD", [])
    imagery = req.get("PREFERRED_IMAGERY", [])
    chains_target = req.get("CHAINS_COUNT", 120)
    mode = req.get("MODE_SELECTION", "CHAOS").strip().upper()
    selection_b_count = req.get("SELECTION_B_COUNT", 8)
    refining_count = req.get("REFINING_COUNT", 2)
    direction = req.get("DIRECTION", "")
    step1_executor = (req.get("STEP1_EXECUTOR") or "GEMINI_CLI").strip().upper()
    if step1_executor not in ("GEMINI_CLI", "SELF"):
        step1_executor = "GEMINI_CLI"

    import re
    is_korean = bool(re.search("[가-힣]", starting + direction))
    word_pool_path = get_word_pool_path(factory_instance, is_korean)

    if step == 1:
        chains_done = factory_instance.count_lines(factory_instance.section_a_path)
        if chains_done >= chains_target:
            state["current_step"] = 2
            factory_instance.save_state(state)
            return f"STEP 1 COMPLETE ({chains_done} chains). Advancing to Step 2. Call run_delusionist again."

        if step1_executor == "GEMINI_CLI":
            try:
                step1_batch_size = req.get("STEP1_BATCH_SIZE", 30)
                info = factory_instance.prepare_step1_gemini_prompt(batch_size=step1_batch_size)
            except Exception as e:
                return f"STEP 1 is external (Gemini CLI), but prompt preparation failed: {e}"
            return f"""
=== STEP 1: EXTERNAL (Gemini CLI) ===
Progress: {chains_done}/{chains_target}

NOTE:
- MCP/Agent deliberately SKIPS generating Step 1.
- Run Gemini CLI locally, then append the resulting lines to:
  {factory_instance.section_a_path}

Batch / ETA:
- Batch: {info['batch_index']}/{info['total_batches']} (this batch writes lines {info['batch_start']}~{info['batch_end']})
- ETA (rough): {info['eta_text']}

Prompt File:
- {info['prompt_path']}

Command:
- {info['cmd']}

After you append enough lines, call run_delusionist again and it will advance to Step 2.
"""

        BATCH_SIZE = req.get("STEP1_BATCH_SIZE", 30)
        remaining = chains_target - chains_done
        current_batch = min(BATCH_SIZE, remaining)
        batch_start = chains_done + 1
        batch_end = chains_done + current_batch

        batch_random_words = []
        for _ in range(current_batch):
            batch_random_words.append(get_random_words_from_file(word_pool_path, 3))

        random_words_section = "\n".join([
            f"  [{i:03d}] {', '.join(words)}"
            for i, words in enumerate(batch_random_words, start=batch_start)
        ])

        return f"""
=== STEP 1: CHAINING (SELF) ===
Batch: #{batch_start} ~ #{batch_end} / {chains_target}
Progress: {chains_done}/{chains_target}

CONFIG:
- Starting Sentence: {starting}
- Mandatory Words: {', '.join(mandatory)}
- Mode: {mode}
- Preferred Imagery: {', '.join(imagery)}

RANDOM WORDS FOR THIS BATCH:
{random_words_section}

YOUR TASK:
1. Generate {current_batch} delusional variant sentences using the random words above.
2. MUST include mandatory words ({', '.join(mandatory)}) in EVERY sentence.
3. LANGUAGE RULE: no 3+ consecutive foreign words when mixing Korean/English.
4. Call append_result with step="1" and your generated sentences.

After appending, call run_delusionist again to continue.
"""

    elif step == 2:
        refined_done = factory_instance.count_lines(factory_instance.section_b_path)
        if refined_done >= selection_b_count:
            state["current_step"] = 3
            factory_instance.save_state(state)
            return f"STEP 2 COMPLETE ({refined_done} refined). Advancing to Step 3. Call run_delusionist again."
        BATCH_SIZE = 10
        remaining = selection_b_count - refined_done
        current_batch = min(BATCH_SIZE, remaining)
        return f"""
=== STEP 2: REFINING CoT ===
Progress: {refined_done}/{selection_b_count}

CONFIG:
- Direction: {direction[:100]}...
- Preferred Imagery: {', '.join(imagery)}

YOUR TASK (v3 rules):
1. Read section_a_chains.txt (read_output_file with step="1").
2. Apply: depth-preserve / strip-abstraction, PRUNING ("ingenious but not absurd"),
   naming as default (original + grounded; retreat to plain phrasing only when truly impossible).
3. Generate {current_batch} refined entries; if fewer survive your self-censor, end short — the
   next batch picks up the slack. Each entry MUST end with a parenthetical annotation.
4. Call append_result with step="2".

After appending, call run_delusionist again.
"""

    elif step == 3:
        final_done = factory_instance.count_lines(factory_instance.section_c_path)
        step3_finalized = state.get("step3_finalized", False)
        if step3_finalized:
            return f"""
=== ALL STEPS COMPLETE ===
Section A (Chains): {os.path.basename(factory_instance.section_a_path)}
Section B (Refined): {os.path.basename(factory_instance.section_b_path)}
Section C (Final): {os.path.basename(factory_instance.section_c_path)}

Use read_output_file with step="3" to view final results.
"""
        return f"""
=== STEP 3: FINAL CoT ===
Progress: {final_done} lines appended (target: {refining_count} entries, not finalized)

YOUR TASK:
1. Read section_b_refined.txt (read_output_file with step="2").
2. Pick ONE Main Idea (>=30% of the final piece's volume/depth) before writing.
3. Restraint on naming: only name if (a) one-of-a-kind in reality AND (b) compressing into a single
   word produces semantic/economic gain across two or more later references. Otherwise plain phrase.
4. Beat the [expectations] block; stay vertically inside DIRECTION's frame.
5. Produce {refining_count} final piece(s).
6. Call append_result with step="3"; on the LAST append, pass finalize=true.

After finalizing, call run_delusionist to confirm completion.
"""

    return "ERROR: Invalid step number"


# ── Tool list ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ── Agent lifecycle ───────────────────────────────────────────
        Tool(
            name="register_agent",
            description="""[CALL THIS FIRST] Register a new agent and obtain an `agent_id`.

The server issues a fresh id (e.g. 'a-3f2k7d9c') and creates an isolated workspace
(output/, staging/, input/, mini/staging/) scoped to this id. Pass the returned id as
'agent_id' to every subsequent tool call.

Sliding 24h TTL: every successful tool call extends the expiry. Expired agents are
swept lazily on each register_agent call. When done, call release_agent for prompt
cleanup.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "ttl_hours": {"type": "integer", "default": 24, "description": "Sliding TTL window (hours, default 24)."}
                },
                "required": []
            }
        ),
        Tool(
            name="release_agent",
            description="Immediately delete the agent's workspace and remove it from the registry. Idempotent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent id obtained from register_agent."}
                },
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="list_agents",
            description="List all currently registered agents and their TTL metadata. Read-only / debug.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),

        # ── Reference doc (no agent needed) ──────────────────────────
        Tool(
            name="get_request_guide",
            description="""[CALL THIS EARLY] Get the full REQUEST_GUIDE.md — the operations manual for
Delusionist Factory. Explains the 3-step pipeline, all request.json fields, the DIRECTION 6-Layer
framework, anti-patterns, and the multi-agent isolation model. Read this before issuing tool calls
for the first time in a session.""",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),

        # ── Pipeline tools (require agent_id) ────────────────────────
        Tool(
            name="run_delusionist",
            description="""Execute Delusionist Factory for the given agent. Returns the next-step task instructions.

- Step 1: external Gemini CLI prompt + cmd (when STEP1_EXECUTOR=GEMINI_CLI), or in-agent task block (SELF).
- Step 2: Refining CoT — depth preserve / strip abstraction.
- Step 3: Final CoT — pick a Main Idea, beat the expectation ceiling, finalize when done.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "config_update": {"type": "object", "description": "Optional config update merged into this agent's request.json."}
                },
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="get_status",
            description="Get the agent's current pipeline progress (current_step, per-step counts, finalized flag).",
            inputSchema={
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="append_result",
            description="""Append generated sentences to the agent's output file for the given step.

Arguments:
- agent_id
- step: '1' | '2' | '3'
- content: newline-separated sentences
- finalize: (Step 3 only) true on the LAST append to mark Step 3 complete.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "step": {"type": "string", "enum": ["1", "2", "3"]},
                    "content": {"type": "string"},
                    "finalize": {"type": "boolean", "default": False}
                },
                "required": ["agent_id", "step", "content"]
            }
        ),
        Tool(
            name="get_request_config",
            description="Get the agent's current request.json configuration.",
            inputSchema={
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="update_request_config",
            description="""Update the agent's request.json configuration (merges into existing).

Numeric bounds applied: CHAINS_COUNT [1,700], SELECTION_B_COUNT [1,200], REFINING_COUNT [1,200],
STEP1_BATCH_SIZE [1,100].""",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "config": {"type": "object", "description": "Configuration object to merge."}
                },
                "required": ["agent_id", "config"]
            }
        ),
        Tool(
            name="reset_factory",
            description="Wipe the agent's output/ and staging/ files (keeps request.json). Requires confirm=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "confirm": {"type": "boolean"}
                },
                "required": ["agent_id", "confirm"]
            }
        ),
        Tool(
            name="get_random_words",
            description="Sample N random words from the (language-appropriate) word pool for the agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "count": {"type": "integer", "default": 3}
                },
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="read_output_file",
            description="Read the agent's section_a/b/c output file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "step": {"type": "string", "enum": ["1", "2", "3"]}
                },
                "required": ["agent_id", "step"]
            }
        ),
        Tool(
            name="prepare_parallel_gemini_workers",
            description="""Step 1 parallel Gemini CLI workers — split the remaining chains into N workers,
write a prompt per worker to the agent's staging/, and return the gemini cmd per worker.

Two modes (batch_size takes precedence):
- batch_size: chains per worker, worker_count auto-computed.
- worker_count: number of workers, chains evenly distributed.
Both unset -> batch_size=25 default.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "worker_count": {"type": "integer"},
                    "batch_size": {"type": "integer"}
                },
                "required": ["agent_id"]
            }
        ),

        # ── Mini tools ───────────────────────────────────────────────
        Tool(
            name="prepare_mini_step1_workers",
            description="""[Mini] Step 1 (chaining) workers, scoped to this agent's mini/staging/.

Two execution modes (controlled by `executor`):
- "GEMINI" (default): returns gemini CLI cmd per worker.
- "SELF": orchestrator runs the prompt directly (cmd not returned, prompt body is).

Returns: {agent_id, executor, final_language, total_workers, total_lines, workers: [{worker_id, line_count, prompt_path, prompt, cmd?}]}.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "chains_count": {"type": "integer"},
                    "chains_per_worker": {"type": "integer", "default": 25},
                    "direction": {"type": "string"},
                    "starting": {"type": "string", "default": ""},
                    "mandatory": {"type": "array", "items": {"type": "string"}, "default": []},
                    "imagery": {"type": "array", "items": {"type": "string"}, "default": []},
                    "mode": {"type": "string", "enum": ["CHAOS", "NUANCE"], "default": "NUANCE"},
                    "final_language": {"type": "string", "default": "Auto"},
                    "executor": {"type": "string", "enum": ["GEMINI", "SELF"], "default": "GEMINI"},
                    "model": {"type": "string", "default": ""}
                },
                "required": ["agent_id", "chains_count", "direction"]
            }
        ),
        Tool(
            name="prepare_mini_step1_1_workers",
            description="""[Mini] Step 1-1 (PPB ~1/5 discard + idea conversion) workers, scoped to this
agent's mini/staging/. Same GEMINI / SELF executor split.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "chains": {"type": "array", "items": {"type": "string"}},
                    "chains_per_worker": {"type": "integer", "default": 25},
                    "direction": {"type": "string"},
                    "mandatory": {"type": "array", "items": {"type": "string"}, "default": []},
                    "final_language": {"type": "string", "default": "Auto"},
                    "executor": {"type": "string", "enum": ["GEMINI", "SELF"], "default": "GEMINI"},
                    "model": {"type": "string", "default": ""}
                },
                "required": ["agent_id", "chains", "direction"]
            }
        ),
        Tool(
            name="parse_mini_step1_response",
            description="[Mini] Parse a raw Gemini CLI response (or any numbered text) and return only lines starting with a 3-digit number. Stateless — no agent_id needed.",
            inputSchema={
                "type": "object",
                "properties": {"raw": {"type": "string"}},
                "required": ["raw"]
            }
        ),
        Tool(
            name="parse_mini_step1_1_response",
            description="[Mini] Parse a raw Gemini CLI response (or any one-line-per-idea text) and return idea lines. Stateless.",
            inputSchema={
                "type": "object",
                "properties": {"raw": {"type": "string"}},
                "required": ["raw"]
            }
        ),

        # ── Skill download (no agent needed) ─────────────────────────
        Tool(
            name="get_skill_delutionist",
            description="""[Skill download] Returns the bundled `delusionist` Claude Code skill — a full SKILL.md plus
any auxiliary files — packaged as JSON the client can dump straight to `~/.claude/skills/delusionist/`.

Response shape:
{
  "skill_name": "delusionist",
  "base_path": "~/.claude/skills/delusionist",
  "files": [{"path": "SKILL.md", "content": "..."}, ...],
  "instruction": "Save each file at <base_path>/<path>; mkdir -p; restart to take effect."
}

Note: the directory name on disk stays `delusionist` (not `delutionist`) for backwards
compat with existing references inside the skill.""",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="get_skill_delutionist_mini",
            description="""[Skill download] Returns the bundled `delusionist-mini` Claude Code skill.

Same response shape as get_skill_delutionist. Save under `~/.claude/skills/delusionist-mini/`.""",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
    ]


# ── Tool dispatch ─────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    # ── No-agent tools ────────────────────────────────────────────────
    if name == "get_request_guide":
        guide_path = os.path.join(BASE_DIR, "REQUEST_GUIDE.md")
        if not os.path.exists(guide_path):
            return [TextContent(type="text", text="ERROR: REQUEST_GUIDE.md not found")]
        with open(guide_path, 'r', encoding='utf-8') as f:
            return [TextContent(type="text", text=f.read())]

    elif name == "register_agent":
        ttl = int(arguments.get("ttl_hours") or 24)
        info = registry.register(ttl_hours=ttl)
        return [TextContent(type="text", text=json.dumps(info, ensure_ascii=False, indent=2))]

    elif name == "release_agent":
        aid = str(arguments.get("agent_id", "")).strip()
        if not aid:
            return [TextContent(type="text", text="ERROR: agent_id is required")]
        ok = registry.release(aid)
        if ok:
            return [TextContent(type="text", text=f"SUCCESS: agent_id '{aid}' released and workspace deleted.")]
        return [TextContent(type="text", text=f"NOOP: agent_id '{aid}' was unknown or already released.")]

    elif name == "list_agents":
        agents = registry.list_all()
        return [TextContent(type="text", text=json.dumps(
            {"count": len(agents), "agents": agents},
            ensure_ascii=False, indent=2,
        ))]

    elif name == "parse_mini_step1_response":
        try:
            from mini.core import parse_step1_response
        except ImportError as e:
            return [TextContent(type="text", text=f"ERROR: mini module not importable: {e}")]
        return [TextContent(
            type="text",
            text=json.dumps(parse_step1_response(str(arguments.get("raw", ""))), ensure_ascii=False),
        )]

    elif name == "parse_mini_step1_1_response":
        try:
            from mini.core import parse_step1_1_response
        except ImportError as e:
            return [TextContent(type="text", text=f"ERROR: mini module not importable: {e}")]
        return [TextContent(
            type="text",
            text=json.dumps(parse_step1_1_response(str(arguments.get("raw", ""))), ensure_ascii=False),
        )]

    elif name == "get_skill_delutionist":
        return _read_skill("delusionist")

    elif name == "get_skill_delutionist_mini":
        return _read_skill("delusionist-mini")

    # ── All remaining tools require a valid agent_id ──────────────────
    factory, err = _resolve_agent(arguments)
    if err is not None:
        return err
    aid = factory.agent_id

    if name == "run_delusionist":
        config_update = arguments.get("config_update")
        if config_update:
            current = factory.load_request() or {}
            current.update(config_update)
            with open(factory.request_path, 'w', encoding='utf-8') as f:
                json.dump(current, f, ensure_ascii=False, indent=2)

        state = factory.load_state()
        step = state.get("current_step", 1)
        if step == 1:
            req = factory.load_request() or {}
            step1_executor = (req.get("STEP1_EXECUTOR") or "GEMINI_CLI").strip().upper()
            chains_target = req.get("CHAINS_COUNT", 120)
            chains_done = factory.count_lines(factory.section_a_path)
            if step1_executor == "GEMINI_CLI" and chains_done < chains_target:
                return [TextContent(type="text", text=get_step_instructions(1, factory))]

        import subprocess
        result = subprocess.run(
            [sys.executable, "main.py"],
            cwd=factory.base_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "DELUSIONIST_AGENT_ID": aid},
        )
        if result.returncode != 0:
            print(f"[run_delusionist:{aid}] stderr: {result.stderr}", file=sys.stderr)
            return [TextContent(type="text", text=f"Error running pipeline (exit {result.returncode}). Check server logs.")]
        return [TextContent(type="text", text=result.stdout or "No output from main.py")]

    elif name == "get_status":
        state = factory.load_state()
        req = factory.load_request()
        if not req:
            return [TextContent(type="text", text="ERROR: request.json not found. Use update_request_config first.")]

        chains_done = factory.count_lines(factory.section_a_path)
        refined_done = factory.count_lines(factory.section_b_path)
        final_done = factory.count_lines(factory.section_c_path)
        step3_finalized = state.get("step3_finalized", False)

        if step3_finalized:
            step3_display = f"finalized ({final_done} lines)"
        elif final_done > 0:
            step3_display = f"{final_done} lines appended (not finalized)"
        else:
            step3_display = f"0/{req.get('REFINING_COUNT', 2)}"

        status = {
            "agent_id": aid,
            "current_step": state.get("current_step", 1),
            "progress": {
                "step1_chains": f"{chains_done}/{req.get('CHAINS_COUNT', 120)}",
                "step2_refined": f"{refined_done}/{req.get('SELECTION_B_COUNT', 8)}",
                "step3_final": step3_display,
            },
            "mode": req.get("MODE_SELECTION", "CHAOS"),
            "starting_sentence": req.get("STARTING_SENTENCE", ""),
        }
        return [TextContent(type="text", text=json.dumps(status, ensure_ascii=False, indent=2))]

    elif name == "append_result":
        step_str = str(arguments.get("step", ""))
        content = str(arguments.get("content", ""))
        finalize = bool(arguments.get("finalize", False))
        try:
            step = int(step_str)
        except (ValueError, TypeError):
            return [TextContent(type="text", text=f"ERROR: Invalid step '{step_str}'. Must be '1', '2', or '3'.")]
        if step == 1:
            filepath = factory.section_a_path
        elif step == 2:
            filepath = factory.section_b_path
        elif step == 3:
            filepath = factory.section_c_path
        else:
            return [TextContent(type="text", text=f"ERROR: Invalid step {step}. Must be 1, 2, or 3.")]

        factory.locked_append(filepath, content)
        lines_added = len([l for l in content.strip().split('\n') if l.strip()])
        msg = f"SUCCESS: appended {lines_added} lines to {os.path.basename(filepath)} (agent {aid})"
        if finalize and step == 3:
            state = factory.load_state()
            state["step3_finalized"] = True
            factory.save_state(state)
            msg += " | FINALIZED: Step 3 marked as complete."
        return [TextContent(type="text", text=msg)]

    elif name == "get_request_config":
        req = factory.load_request()
        if not req:
            return [TextContent(type="text", text="ERROR: request.json not found")]
        return [TextContent(type="text", text=json.dumps(req, ensure_ascii=False, indent=2))]

    elif name == "update_request_config":
        config = arguments.get("config") or {}
        _NUMERIC_LIMITS = {
            "CHAINS_COUNT": (1, 700),
            "SELECTION_B_COUNT": (1, 200),
            "REFINING_COUNT": (1, 200),
            "STEP1_BATCH_SIZE": (1, 100),
        }
        for key, (lo, hi) in _NUMERIC_LIMITS.items():
            if key in config and isinstance(config[key], (int, float)):
                config[key] = max(lo, min(hi, int(config[key])))
        with FileLock(factory.config_lock_path):
            current = factory.load_request() or {}
            current.update(config)
            with open(factory.request_path, 'w', encoding='utf-8') as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
        return [TextContent(type="text", text=f"SUCCESS: updated request.json for agent {aid}")]

    elif name == "reset_factory":
        if not arguments.get("confirm", False):
            return [TextContent(type="text", text="ERROR: confirm must be true to reset")]
        for d in (factory.output_dir, factory.staging_dir):
            if os.path.exists(d):
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    if os.path.isfile(fp):
                        os.remove(fp)
        return [TextContent(type="text", text=f"SUCCESS: factory reset for agent {aid}")]

    elif name == "get_random_words":
        count = min(max(int(arguments.get("count", 3)), 1), 100)
        req = factory.load_request()
        if not req:
            return [TextContent(type="text", text="ERROR: request.json not found")]
        starting = req.get("STARTING_SENTENCE", "")
        direction = req.get("DIRECTION", "")
        final_lang = req.get("FINAL_LANGUAGE", "").strip().upper()
        if final_lang == "KOREAN":
            is_korean = True
        elif final_lang == "ENGLISH":
            is_korean = False
        else:
            import re
            is_korean = bool(re.search("[가-힣]", starting + direction))
        word_pool_path = get_word_pool_path(factory, is_korean)
        words = get_random_words_from_file(word_pool_path, count)
        return [TextContent(type="text", text=json.dumps(words, ensure_ascii=False))]

    elif name == "read_output_file":
        step_str = str(arguments.get("step", ""))
        try:
            step = int(step_str)
        except (ValueError, TypeError):
            return [TextContent(type="text", text=f"ERROR: Invalid step '{step_str}'. Must be '1', '2', or '3'.")]
        if step == 1:
            filepath = factory.section_a_path
        elif step == 2:
            filepath = factory.section_b_path
        elif step == 3:
            filepath = factory.section_c_path
        else:
            return [TextContent(type="text", text=f"ERROR: Invalid step {step}. Must be 1, 2, or 3.")]
        if not os.path.exists(filepath):
            return [TextContent(type="text", text=f"File not found (empty): {os.path.basename(filepath)}")]
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        if not content.strip():
            return [TextContent(type="text", text=f"File is empty: {os.path.basename(filepath)}")]
        return [TextContent(type="text", text=content)]

    elif name == "prepare_parallel_gemini_workers":
        batch_size = arguments.get("batch_size")
        worker_count = arguments.get("worker_count")
        if batch_size is not None and batch_size < 1:
            return [TextContent(type="text", text="ERROR: batch_size must be >= 1")]
        if worker_count is not None and worker_count < 1:
            return [TextContent(type="text", text="ERROR: worker_count must be >= 1")]
        try:
            workers = factory.prepare_parallel_gemini_workers(
                worker_count=worker_count,
                batch_size=batch_size,
            )
        except RuntimeError as e:
            return [TextContent(type="text", text=f"ERROR: {e}")]
        if not workers:
            return [TextContent(type="text", text="Step 1 already complete. Call run_delusionist to advance.")]
        result = {
            "agent_id": aid,
            "total_workers": len(workers),
            "total_lines": sum(w["line_count"] for w in workers),
            "workers": workers,
        }
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    elif name == "prepare_mini_step1_workers":
        try:
            from mini.core import DelutionistConfig, split_step1_workers, detect_language
        except ImportError as e:
            return [TextContent(type="text", text=f"ERROR: mini module not importable: {e}")]

        chains_count = int(arguments.get("chains_count", 0))
        if chains_count < 1:
            return [TextContent(type="text", text="ERROR: chains_count must be >= 1")]
        direction = str(arguments.get("direction", "")).strip()
        if not direction:
            return [TextContent(type="text", text="ERROR: direction is required")]
        starting = str(arguments.get("starting", ""))
        mandatory = list(arguments.get("mandatory") or [])
        imagery = list(arguments.get("imagery") or [])
        mode = str(arguments.get("mode", "NUANCE")).upper()
        final_language_in = str(arguments.get("final_language", "Auto"))
        executor = str(arguments.get("executor", "GEMINI")).upper()
        if executor not in ("GEMINI", "SELF"):
            executor = "GEMINI"
        model = str(arguments.get("model", ""))
        chains_per_worker = max(1, int(arguments.get("chains_per_worker", 25)))

        fl_norm = final_language_in.strip().upper()
        if fl_norm == "KOREAN":
            final_language = "Korean"
        elif fl_norm == "ENGLISH":
            final_language = "English"
        else:
            final_language = detect_language(starting + direction)

        cfg = DelutionistConfig(
            direction=direction,
            starting=starting,
            mandatory=mandatory,
            imagery=imagery,
            mode=mode,
            final_language=final_language,
        )

        mini_staging = _mini_staging_dir(aid)
        try:
            workers = split_step1_workers(
                cfg=cfg,
                total_chains=chains_count,
                chains_per_worker=chains_per_worker,
                staging_dir=mini_staging,
                model=model,
            )
        except Exception as e:
            return [TextContent(type="text", text=f"ERROR: split_step1_workers failed: {e}")]

        workers_out = []
        for w in workers:
            try:
                prompt_text = w.prompt_path.read_text(encoding="utf-8")
            except Exception:
                prompt_text = ""
            item = {
                "worker_id": w.worker_id,
                "line_count": w.line_count,
                "prompt_path": str(w.prompt_path),
                "prompt": prompt_text,
            }
            if executor == "GEMINI":
                item["cmd"] = w.cmd
            workers_out.append(item)

        result = {
            "agent_id": aid,
            "executor": executor,
            "final_language": final_language,
            "total_workers": len(workers),
            "total_lines": sum(w.line_count for w in workers),
            "workers": workers_out,
        }
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    elif name == "prepare_mini_step1_1_workers":
        try:
            from mini.core import DelutionistConfig, split_step1_1_workers, detect_language
        except ImportError as e:
            return [TextContent(type="text", text=f"ERROR: mini module not importable: {e}")]

        chains = list(arguments.get("chains") or [])
        if not chains:
            return [TextContent(type="text", text="ERROR: chains must be a non-empty list")]
        direction = str(arguments.get("direction", "")).strip()
        if not direction:
            return [TextContent(type="text", text="ERROR: direction is required")]
        mandatory = list(arguments.get("mandatory") or [])
        final_language_in = str(arguments.get("final_language", "Auto"))
        executor = str(arguments.get("executor", "GEMINI")).upper()
        if executor not in ("GEMINI", "SELF"):
            executor = "GEMINI"
        model = str(arguments.get("model", ""))
        chains_per_worker = max(1, int(arguments.get("chains_per_worker", 25)))

        fl_norm = final_language_in.strip().upper()
        if fl_norm == "KOREAN":
            final_language = "Korean"
        elif fl_norm == "ENGLISH":
            final_language = "English"
        else:
            final_language = detect_language(direction + " ".join(chains[:5]))

        cfg = DelutionistConfig(
            direction=direction,
            starting="",
            mandatory=mandatory,
            imagery=[],
            mode="NUANCE",
            final_language=final_language,
        )

        mini_staging = _mini_staging_dir(aid)
        try:
            workers = split_step1_1_workers(
                cfg=cfg,
                chains=chains,
                chains_per_worker=chains_per_worker,
                staging_dir=mini_staging,
                model=model,
            )
        except Exception as e:
            return [TextContent(type="text", text=f"ERROR: split_step1_1_workers failed: {e}")]

        workers_out = []
        for w in workers:
            try:
                prompt_text = w.prompt_path.read_text(encoding="utf-8")
            except Exception:
                prompt_text = ""
            item = {
                "worker_id": w.worker_id,
                "line_count": w.line_count,
                "prompt_path": str(w.prompt_path),
                "prompt": prompt_text,
            }
            if executor == "GEMINI":
                item["cmd"] = w.cmd
            workers_out.append(item)

        result = {
            "agent_id": aid,
            "executor": executor,
            "final_language": final_language,
            "total_workers": len(workers),
            "total_kept_lines": sum(w.line_count for w in workers),
            "workers": workers_out,
        }
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    return [TextContent(type="text", text=f"ERROR: Unknown tool '{name}'")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

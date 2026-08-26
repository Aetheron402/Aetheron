import os, re, sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import requests
import json

# Must be selected before pyplot is imported. Celery forks its workers, and a
# GUI-capable backend in a forked, headless container hangs on the first render.
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from PIL import Image
from datetime import datetime
from dotenv import load_dotenv
from celery import Celery
from celery.worker.control import control_command
from storage import store_asset
from pydantic import BaseModel, Field

import llm
from bs4 import BeautifulSoup

from pdf_utils import build_aetheron_pdf
from export_utils import export_generic
from asset_naming import asset_filename
from ledger_utils import finalize_asset

load_dotenv()


SNAPSHOT_DIR = "./contract_snapshots"
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

def snapshot_path(address, network):
    safe = f"{network}_{address}".replace("/", "_")
    return os.path.join(SNAPSHOT_DIR, safe + ".json")

def store_contract_snapshot(address, network, data):
    try:
        with open(snapshot_path(address, network), "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def load_last_snapshot(address, network):
    try:
        with open(snapshot_path(address, network), "r") as f:
            return json.load(f)
    except Exception:
        return None


print("CELERY WORKER LOADING UPDATED FILE")


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
HONEYPOT_API_KEY = os.getenv("HONEYPOT_API_KEY")

BUBBLEMAP_MODE = os.getenv("BUBBLEMAP_MODE", "both").lower()

BUBBLEMAP_MAX_HOLDERS = int(os.getenv("BUBBLEMAP_MAX_HOLDERS", "10"))
BUBBLEMAP_MAX_ETH_TX   = int(os.getenv("BUBBLEMAP_MAX_ETH_TX", "250"))
BUBBLEMAP_MAX_SOL_TX   = int(os.getenv("BUBBLEMAP_MAX_SOL_TX", "100"))


celery = Celery(
    "aetheron",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery.conf.update(
    worker_max_tasks_per_child=1,
    worker_max_memory_per_child=250000,  # 250 MB
    worker_prefetch_multiplier=1,
    task_acks_late=False,
)


@control_command()
def storage_backend(state):
    """
    Report which store this worker writes finished reports to.

    The worker and the web process are separate containers with separate
    environments, so one can be pointed at Postgres while the other quietly
    falls back to its own SQLite file. Nothing fails when that happens: the
    worker generates the report, the customer is charged, and the file lands
    somewhere the process serving /download cannot read it from.

    Answering over the control channel the status page already pings lets it
    compare the two backends instead of assuming they match.
    """
    import storage
    return {"backend": storage.backend_name()}


client = None


# MARKDOWN CLEANER
# Written as escapes, not literal characters, so a search-and-replace over this
# file for long dashes cannot silently rewrite the pattern that removes them.
_LONG_DASH = "—–"
_DASH_BETWEEN_DIGITS = re.compile(rf"(?<=\d)\s*[{_LONG_DASH}]\s*(?=\d)")
_DASH_ANYWHERE = re.compile(rf"\s*[{_LONG_DASH}]\s*")


def strip_long_dashes(text: str) -> str:
    """
    Remove em and en dashes from generated copy.

    run_llm instructs the model to avoid them, but models reach for them anyway,
    so the guarantee is enforced here where it is deterministic. A dash between
    digits is a range and keeps a hyphen: rewriting "2020-2024" as "2020, 2024"
    would change what the report says.

    Runs last in clean_markdown, so the certification and rule patterns above
    still see the text in the form the model produced it.
    """
    text = _DASH_BETWEEN_DIGITS.sub("-", text)
    return _DASH_ANYWHERE.sub(", ", text)


def clean_markdown(md: str) -> str:
    """
    This cleanup function is intentionally synchronized with the
    preprocessing rules inside pdf_utils.py to ensure:

      - metric lines stay preserved (e.g. "Readability: 7/10")
      - bullets remain intact
      - code blocks are NOT removed
      - certification blocks are removed
      - decorative markup removed
    """

    text = md or ""
    text = text.strip()

    text = re.sub(r"(?m)^---+$", "", text)

    text = re.sub(r"\*\*", "", text)

    text = re.sub(r"■", "", text)

    text = re.sub(r"(?s)>\s*🧾\s*\*?Certified.*", "", text)
    text = re.sub(r"(?s)Aetheron X402 [—-] Certified Asset.*", "", text)
    text = re.sub(r"(?s)Certified Aetheron Asset.*", "", text)

    text = re.sub(r"(\d+)\s*/\s*(\d+)", r"\1/\2", text)

    text = text.replace("(/10)", "0/10")

    text = re.sub(r"(?m)^\s*\|[-\s]+\|\s*$", "", text)

    text = strip_long_dashes(text)

    return text.strip()

def now_stamp():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def generate_pdf(asset_id, wallet, title, subtitle, md_text, chart_path=None):
    """
    Generates PDF, uploads to R2, returns (filename, public_url)
    """
    buffer, fname = build_aetheron_pdf(
        asset_id=asset_id,
        timestamp=now_stamp(),
        wallet=wallet,
        title=title,
        subtitle=subtitle,
        md_text=md_text,
        chart_path=chart_path,
    )

    r2_url = store_asset(buffer.getvalue(), fname)

    return fname, r2_url

def generate_txt(md_text, asset_id="asset"):
    """
    Generates TXT as bytes, uploads to R2, returns (filename, public_url)
    """
    fname = asset_filename(asset_id, "txt")
    data = "\ufeff" + md_text
    data = data.encode("utf-8")

    r2_url = store_asset(data, fname)

    return fname, r2_url


HOUSE_STYLE = (
    "Never use em dashes or en dashes. Use a comma, a colon, a full stop or "
    "brackets instead. This applies to every line you write, including headings, "
    "bullet points and table cells."
)


def run_llm(system_prompt, user_payload, style_note):
    """
    Unified invoker. Instructions are ordered stable first so the prefix caches
    across every call to the same component.
    """
    raw = llm.complete(
        system_blocks=[HOUSE_STYLE, style_note, system_prompt],
        user_payload=user_payload,
    )
    return clean_markdown(raw)

# What the optimizer returns. Asking for named fields means a section cannot be
# merged, renamed or dropped, so the renderer below is exact rather than a regex
# hunting for headings in free text.
class OptimizedPrompt(BaseModel):
    optimized_prompt: str = Field(
        description="The rewritten prompt, ready to paste and run. No preamble, no commentary."
    )
    what_changed: list[str] = Field(
        description="Each edit made and the concrete failure it prevents. As many as the prompt warranted."
    )
    analysis: str = Field(
        description="What the original aimed at, what it got right, where it was ambiguous or under-constrained."
    )
    failure_modes: list[str] = Field(
        description="How the original would fail in practice: misreadings, edge cases, confidently wrong output."
    )
    variants: list[str] = Field(
        default_factory=list,
        description="Alternative rewrites serving a genuinely different goal. Empty when the prompt admits one reading.",
    )
    usage_notes: list[str] = Field(
        description="Deploying the rewrite: what to watch, what to adjust per model, what to measure."
    )


def _render_optimizer_report(r: "OptimizedPrompt", target_label: str) -> str:
    """Turn the structured result into the markdown the document engine expects."""
    def bullets(items):
        return "\n".join(f"• {i}" for i in items) if items else ""

    parts = [
        "1. Optimized Prompt\n",
        r.optimized_prompt.strip(),
        "\n\n2. What Changed\n",
        bullets(r.what_changed),
        "\n\n3. Prompt Analysis\n",
        r.analysis.strip(),
        "\n\n4. Failure Modes\n",
        bullets(r.failure_modes),
    ]
    if r.variants:
        parts += ["\n\n5. Variants\n", bullets(r.variants)]
    else:
        parts += ["\n\n5. Variants\n",
                  "This prompt admits one sensible reading, so no alternative angle would serve it better."]
    parts += ["\n\n6. Using It\n", bullets(r.usage_notes)]

    if target_label:
        parts.insert(0, f"Optimized for: {target_label}\n\n")
    return "".join(parts)


@celery.task(name="process_prompt")
def process_prompt(asset_id, user_text, out_format, wallet, target=None):
    SYSTEM_PROMPT = """
    You are Aetheron. You rewrite prompts so they work, and you explain what you
    changed.

    The person reading this paid for a better prompt. The rewrite is the product;
    the analysis exists to justify it and to teach them what to do next time. Lead
    with the rewrite.

    Structure:

    1. Optimized Prompt

       The rewritten prompt, ready to paste and run. Nothing else in this section,
       no preamble and no commentary, so it can be copied cleanly.

    2. What Changed

       The specific edits you made and why each one matters. Tie every change to a
       concrete failure it prevents, not to a general principle.

    3. Prompt Analysis

       What the original was trying to achieve, what it got right, and where it was
       ambiguous, under-constrained or open to misreading. Cover what a model would
       plausibly get wrong when handed the original.

    4. Failure Modes

       How the original would fail in practice: likely misinterpretations, edge
       cases, and the situations where it produces confidently wrong output.

    5. Variants

       Alternative rewrites where a genuinely different angle serves a different
       goal. Skip this section when the prompt admits only one sensible reading,
       and say so in a line rather than inventing variants.

    6. Using It

       Practical notes on deploying the rewrite: what to watch for, what to adjust
       per model, what to measure.

    Write each section as long as the prompt genuinely warrants and no longer. A
    two-line prompt with one flaw deserves a short report; a complex system prompt
    with layered problems deserves a thorough one. Padding a thin prompt into a
    long document is a worse answer, not a more generous one.

    Section titles are numbered and sit on their own line. Use bullets for lists,
    prose for reasoning. Do not add certification or footer text, the document
    engine handles that.
    """
    # What the prompt is for changes what "better" means: a coding agent wants
    # explicit constraints and formats, an image model wants concrete visual
    # detail. Without this the model optimises for a generic chat assistant.
    TARGETS = {
        "chat":       "a general chat assistant",
        "coding":     "a coding agent that will write or edit code",
        "agent":      "an autonomous agent that plans and calls tools",
        "image":      "an image generation model",
        "extraction": "a structured data extraction task",
    }
    target_label = TARGETS.get((target or "").lower(), "")
    STYLE_NOTE = (
        f"The rewritten prompt is destined for {target_label}. Optimise for how that "
        "kind of model reads a prompt, and say in What Changed where the target drove "
        "an edit."
        if target_label else
        "The target model is unspecified, so keep the rewrite portable across models "
        "rather than tuned to one."
    )

    result = llm.complete_structured(
        system_blocks=[HOUSE_STYLE, STYLE_NOTE, SYSTEM_PROMPT],
        user_payload=user_text,
        schema=OptimizedPrompt,
    )
    final_md = clean_markdown(_render_optimizer_report(result, target_label))

    fmt = (out_format or "pdf").lower()

    if fmt == "pdf":
        filename, url = generate_pdf(
            asset_id,
            wallet or "DEMO_OK",
            title="AI Prompt Optimizer",
            subtitle="Prompt Refinement & Intelligence Analysis",
            md_text=final_md,
        )
    else:
        buffer, fname = export_generic(fmt, final_md, asset_id)
        url = store_asset(buffer.getvalue(), fname)
        filename = fname

    finalize_asset(asset_id, filename)

    return {
        "download_url": url,
        "filename": filename,
        "format": fmt
    }

# What the code auditor returns. Named fields rather than headings the renderer
# has to find in free text, so a section cannot be merged, renamed or dropped.
class CodeAudit(BaseModel):
    language: str = Field(
        description="The language the submitted code is written in, as its markdown fence tag, "
                    "for example python, javascript, typescript, go, rust, sql. Read it from the "
                    "code itself. Use 'text' only when it genuinely cannot be identified."
    )
    summary: str = Field(
        description="What this code is for, what it does, and the state it is in. "
                    "Written for someone deciding whether to trust it."
    )
    how_it_works: str = Field(
        description="Execution flow, how inputs are processed, how outputs are produced, "
                    "and what it depends on."
    )
    strengths: list[str] = Field(
        description="What the code genuinely does well, and why that matters here. "
                    "Only real strengths. An empty list beats invented praise."
    )
    weaknesses: list[str] = Field(
        description="Defects, fragile patterns and maintainability problems. Each one names "
                    "the specific construct at fault, not a general principle."
    )
    complexity: str = Field(
        description="Time and space behaviour, the bottleneck if there is one, and what "
                    "actually drives cost as input grows."
    )
    security: list[str] = Field(
        description="Concrete vulnerabilities and unsafe assumptions, each with the input or "
                    "condition that triggers it. Empty when the code has no security surface."
    )
    edge_cases: list[str] = Field(
        description="Inputs or states that produce wrong output or a crash. Give the input "
                    "and the resulting behaviour, not a category name."
    )
    refactors: list[str] = Field(
        description="Changes worth making: what to change, and the failure or cost it removes."
    )
    patches: list[str] = Field(
        default_factory=list,
        description="Improved code, each entry a complete replacement for one construct. "
                    "Raw code only, no fences and no commentary; both are added on render.",
    )
    recommendations: list[str] = Field(
        description="What to do first, in order of what carries the most risk."
    )


def _render_code_report(r: "CodeAudit") -> str:
    """Turn the structured audit into the markdown the document engine expects."""
    def bullets(items):
        return "\n".join(f"\u2022 {i}" for i in items) if items else ""

    lang = (r.language or "text").strip().lower() or "text"

    parts = [
        "1. Summary\n", r.summary.strip(),
        "\n\n2. How It Works\n", r.how_it_works.strip(),
    ]
    # Sections that can be genuinely empty say so, rather than being padded to
    # a quota. A clean function with no security surface should report that.
    for title, items, when_empty in [
        ("3. Strengths", r.strengths, "Nothing here rises above ordinary competence."),
        ("4. Weaknesses", r.weaknesses, "No defects found in the submitted code."),
    ]:
        parts += [f"\n\n{title}\n", bullets(items) or when_empty]

    parts += ["\n\n5. Complexity\n", r.complexity.strip()]

    for title, items, when_empty in [
        ("6. Security", r.security, "This code has no security surface: no input crosses a trust boundary."),
        ("7. Edge Cases", r.edge_cases, "No input was found that produces wrong output."),
        ("8. Refactoring", r.refactors, "No change would pay for the risk of making it."),
    ]:
        parts += [f"\n\n{title}\n", bullets(items) or when_empty]

    if r.patches:
        parts.append("\n\n9. Improved Code\n")
        for patch in r.patches:
            body = patch.strip()
            # The model occasionally fences anyway; do not double wrap it.
            if body.startswith("```"):
                parts.append(body + "\n\n")
            else:
                parts.append(f"```{lang}\n{body}\n```\n\n")

    parts += ["\n\n10. Recommendations\n", bullets(r.recommendations)]
    return "".join(parts)


@celery.task(name="process_code")
def process_code(asset_id, code_text, out_format, wallet, features=None):
    SYSTEM_PROMPT = """
You are a senior engineer auditing code someone is about to depend on.

Report what is actually there. The previous version of this brief asked for a
fixed number of bullets per section, which meant a clean function still had to
be given six weaknesses, so the real one was buried among five invented ones.
Do not do that. Every section takes as many findings as the code warrants and
no more, and several may legitimately be empty. A short accurate audit is worth
more than a long one, and the reader is paying for judgement, not volume.

What separates this from a summary anyone could write:

- Name the construct. "The bare except on the retry loop swallows KeyboardInterrupt"
  is a finding. "Error handling could be improved" is not.
- Give the trigger. An edge case is an input plus the behaviour it causes, so
  the reader can reproduce it. A category name is not an edge case.
- Say what breaks. A weakness the reader cannot connect to a consequence reads
  as style preference and will be ignored.
- Distinguish what is wrong from what you would have written differently. Only
  the first belongs in weaknesses.
- Mutable default arguments, unbounded growth, swallowed exceptions, unvalidated
  input reaching a query or a filesystem path, and integer or precision
  assumptions are worth checking every time, but only report them when present.

For patches, give the replacement code raw, with no fences and no commentary.
Both are added when the report is assembled, and the language is taken from the
language field, so identify it from the code rather than assuming.
"""
    STYLE_NOTE = (
        "Write for an experienced engineer. Be specific and concise. "
        "Prose sections are paragraphs, not bullet lists."
    )

    # Front-end code fails in ways a language-agnostic read misses, so when the
    # caller detected these, name the extra ground to cover.
    features = features or {}
    hints = []
    if features.get("contains_jsx"):
        hints.append(
            "This includes JSX or component syntax. Cover component boundaries, "
            "render flow, props and state, and what triggers a re-render."
        )
    if features.get("contains_html"):
        hints.append(
            "This includes HTML markup. Treat structure separately from program logic."
        )
    if features.get("contains_dom"):
        hints.append(
            "This touches the DOM. Cover side effects, lifecycle timing, and what "
            "assumes the document is already parsed."
        )
    if hints:
        SYSTEM_PROMPT += "\n\n" + "\n".join(hints) + "\n"

    audit = llm.complete_structured(
        system_blocks=[HOUSE_STYLE, STYLE_NOTE, SYSTEM_PROMPT],
        user_payload=f"Code for analysis:\n```\n{code_text}\n```",
        schema=CodeAudit,
    )

    final_md = clean_markdown(_render_code_report(audit))

    fmt = (out_format or "pdf").lower()

    if fmt == "pdf":
        filename, url = generate_pdf(
            asset_id,
            wallet or "DEMO_OK",
            title="Code Intelligence Report",
            subtitle="LLM-Powered Code Explainer",
            md_text=final_md,
        )
    else:
        buffer, fname = export_generic(fmt, final_md, asset_id)
        url = store_asset(buffer.getvalue(), fname)
        filename = fname

    finalize_asset(asset_id, filename)

    return {
        "download_url": url,
        "filename": filename,
        "format": fmt
    }

# One simulated reader of the prompt. Nested so a persona cannot lose its
# weakness field, which is the half that carries the finding.
class Persona(BaseModel):
    name: str = Field(description="Who this reader is, in a few words, for example 'Literal-minded junior engineer'.")
    interpretation: str = Field(description="What this persona takes the prompt to be asking for.")
    strength: str = Field(description="What this persona would handle well, and why.")
    weakness: str = Field(description="Where this persona misreads, overfits or fills a gap with an assumption.")
    risks: list[str] = Field(
        default_factory=list,
        description="Specific ways this persona goes wrong: the edge case, the assumption made silently, "
                    "the reasoning path that diverges. Empty when this persona reads the prompt cleanly.",
    )


class PersonaTest(BaseModel):
    interpretation: str = Field(
        description="What the prompt is really asking for, what it leaves unsaid, and which gaps "
                    "different readers would fill differently."
    )
    personas: list[Persona] = Field(
        description="Readers chosen to expose how this specific prompt splits. As many as reveal "
                    "genuine divergence and no more: a tightly specified prompt needs few."
    )
    cross_persona: str = Field(
        description="Where the personas agree, where they genuinely conflict, and which readings "
                    "are stable against which are unpredictable."
    )
    quality_score: int = Field(ge=0, le=10, description="How well specified the prompt is, 0 to 10.")
    quality_reasoning: str = Field(description="What earns and what costs the prompt those points.")
    divergence_score: int = Field(
        ge=0, le=10,
        description="How far the personas' readings spread, 0 to 10. High means the same prompt "
                    "produces materially different work depending on who reads it.",
    )
    divergence_reasoning: str = Field(
        description="Which words or omissions cause the spread, and what that costs in practice."
    )
    improvements: list[str] = Field(
        description="Changes that would close the gaps found above. Each names the ambiguity it removes."
    )
    improved_prompt: str = Field(
        description="The rewritten prompt, ready to paste and run. No preamble, no commentary."
    )


def _render_tester_report(r: "PersonaTest") -> str:
    """Turn the structured persona test into the markdown the document engine expects."""
    def bullets(items):
        return "\n".join(f"\u2022 {i}" for i in items) if items else ""

    parts = ["1. Core Prompt Interpretation\n", r.interpretation.strip(),
             "\n\n2. PersonaBench Matrix\n"]

    for p in r.personas:
        parts.append(f"\n{p.name.strip()}\n\n")
        parts.append(f"\u2022 Interpretation: {p.interpretation.strip()}\n")
        parts.append(f"\u2022 Strength: {p.strength.strip()}\n")
        parts.append(f"\u2022 Weakness: {p.weakness.strip()}\n")

    parts.append("\n\n3. Persona Level Deepening\n")
    deepened = [f"{p.name.strip()}\n\n" + bullets(p.risks) for p in r.personas if p.risks]
    parts.append("\n\n".join(deepened) if deepened
                 else "Every persona read this prompt the same way. Nothing diverged worth reporting.")

    parts += [
        "\n\n4. Cross Persona Comparison\n", r.cross_persona.strip(),
        f"\n\n5. Prompt Quality Score\n\nPrompt Quality Score: {r.quality_score}/10\n\n",
        r.quality_reasoning.strip(),
        f"\n\n6. Persona Divergence Score\n\nPersona Divergence Score: {r.divergence_score}/10\n\n",
        r.divergence_reasoning.strip(),
        "\n\n7. Improvement Suggestions\n", bullets(r.improvements),
        "\n\n8. Improved Prompt Variant\n", r.improved_prompt.strip(),
    ]
    return "".join(parts)


@celery.task(name="process_tester")
def process_tester(asset_id, prompt, out_format, wallet):
    SYSTEM_PROMPT = """
You are PersonaSim. You are given a prompt and you work out how differently
various readers would understand it, so its author learns where it is
ambiguous before it reaches production.

Two rules matter more than the rest.

First, the scores are measurements. The same prompt must receive the same score
every time. An earlier version of this brief demanded the score vary between
runs, which made it a random number wearing the costume of an assessment: a
user running twice saw 6/10 and then 8/10 for identical input and correctly
concluded the number meant nothing. Score what is in front of you. If two
prompts are equally specified they get equal scores, and a well specified
prompt scoring 9 twice is the system working.

Second, choose personas that expose how this particular prompt splits. Not a
fixed roster, and not a fixed count. A prompt with a single sensible reading
needs two personas to demonstrate that agreement. A vague prompt may need five
before the disagreements stop being new. Pick readers whose differences the
prompt actually causes: someone who reads instructions literally against someone
who infers intent, a domain expert against a newcomer, someone optimising for
speed against someone optimising for completeness. The persona is a probe, so
if it does not reveal anything the others missed, leave it out.

What makes a finding useful:

- Quote the words responsible. "'Make it good' gives no measurable target" beats
  "the prompt is vague."
- A weakness is what this persona would actually produce, not a personality
  description.
- Divergence is only worth reporting where it changes the output. Two personas
  phrasing the same answer differently is not divergence.
- Say what the reader silently assumed, since an unstated assumption is where
  the output goes wrong while looking confident.

The improved prompt at the end must close the specific gaps you identified
above, not be a generically longer prompt.
"""
    STYLE_NOTE = (
        "Analytical and concrete. Quote the prompt's own wording when it is "
        "the thing at fault."
    )

    result = llm.complete_structured(
        system_blocks=[HOUSE_STYLE, STYLE_NOTE, SYSTEM_PROMPT],
        user_payload=f"Prompt to test:\n\n{prompt}",
        schema=PersonaTest,
    )

    final_md = clean_markdown(_render_tester_report(result))

    fmt = (out_format or "pdf").lower()

    if fmt == "pdf":
        filename, url = generate_pdf(
            asset_id,
            wallet or "DEMO_OK",
            title="Smart Prompt Tester (PersonaSim)",
            subtitle="Prompt Strength & Persona Analysis",
            md_text=final_md,
        )
    else:
        buffer, fname = export_generic(fmt, final_md, asset_id)
        url = store_asset(buffer.getvalue(), fname)
        filename = fname

    finalize_asset(asset_id, filename)

    return {
        "download_url": url,
        "filename": filename,
        "format": fmt
    }

def fetch_solana_account_info(address: str):
    """
    Fetch detailed Solana account info and classify it as:
    - program
    - token-mint
    - token-account
    - wallet
    """
    if not SOLANA_RPC_URL:
        return {"error": "SOLANA_RPC_URL not configured"}

    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [address, {"encoding": "jsonParsed"}],
        }
        r = requests.post(SOLANA_RPC_URL, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json().get("result", {})
        value = data.get("value")

        if not value:
            return {"error": "Account not found on Solana"}

        parsed = value.get("data", {}).get("parsed", {})
        info = parsed.get("info", {}) if isinstance(parsed, dict) else {}

        kind = "wallet"
        if value.get("executable"):
            kind = "program"
        elif isinstance(parsed, dict):
            if parsed.get("type") == "mint":
                kind = "token-mint"
            elif parsed.get("type") == "account" and info.get("mint"):
                kind = "token-account"

        return {
            "lamports": value.get("lamports"),
            "owner_program": value.get("owner"),
            "executable": value.get("executable"),
            "rent_epoch": value.get("rentEpoch"),
            "parsed_type": parsed.get("type") if isinstance(parsed, dict) else None,
            "parsed_info": info,
            "kind": kind,
            "mint_authority": info.get("mintAuthority") if isinstance(info, dict) else None,
            "freeze_authority": info.get("freezeAuthority") if isinstance(info, dict) else None,

            "supply_raw": info.get("supply"),
            "decimals": info.get("decimals"),
        }
    except Exception as e:
        return {"error": f"Solana RPC error: {e}"}

def resolve_to_mint(addr: str):
    """
    Universal resolver for pump.fun / moonshot / bonkbot / weird UX formats.
    Extracts the FIRST valid 32-byte (44-char Base58) public key from ANY string.
    """
    addr = addr.strip()

    if len(addr) == 44:
        return addr

    import re
    BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    pattern = f"[{BASE58}]{{44}}"

    matches = re.findall(pattern, addr)
    if matches:
        return matches[0]

    return addr[:44]

def fetch_birdeye_full(address: str):
    if not BIRDEYE_API_KEY:
        return {"error": "BIRDEYE_API_KEY not configured"}

    headers = {
        "X-API-KEY": BIRDEYE_API_KEY,
        "accept": "application/json"
    }

    meta = {}
    market = {}
    meta_error = None
    market_error = None

    try:
        meta_url = "https://public-api.birdeye.so/public/token/metadata"
        r1 = requests.get(meta_url, headers=headers, params={"address": address}, timeout=15)
        r1.raise_for_status()
        meta = (r1.json() or {}).get("data", {}) or {}
    except Exception as e:
        meta_error = str(e)

    try:
        market_url = f"https://public-api.birdeye.so/public/market/solana/token/{address}"
        r2 = requests.get(market_url, headers=headers, timeout=15)
        r2.raise_for_status()
        market = (r2.json() or {}).get("data", {}) or {}
    except Exception as e:
        market_error = str(e)

    if not market:
        return {
            "error": "Birdeye returned no market data for this token",
            "meta": meta,
            "meta_error": meta_error,
            "market_error": market_error,
        }

    pools = meta.get("raydiumPools") or []
    pair_address = None
    if pools and isinstance(pools, list):
        first_pool = pools[0] or {}
        pair_address = first_pool.get("pairAddress")

    token_meta = {
        "source": "birdeye",
        "token_name": meta.get("name"),
        "symbol": meta.get("symbol"),
        "decimals": meta.get("decimals"),
        "price_usd": market.get("price"),
        "liquidity_usd": market.get("liquidity"),
        "volume_24h": market.get("volume24h"),
        "fdv": market.get("fdv"),
        "market_cap": market.get("marketCap"),

        "pair_address": pair_address,
        "pair_url": f"https://dexscreener.com/solana/{pair_address}" if pair_address else None,

        "raw": {
            "meta": meta,
            "market": market,
            "meta_error": meta_error,
            "market_error": market_error,
        },
    }

    return token_meta

def build_solana_risk_hints_from_onchain(base_intel, token_meta, helius_holders):
    """
    Build Solana-specific risk hints so the LLM can talk about:
      - authority risk (mint / freeze)
      - holder concentration
      - liquidity thinness

    This does NOT guess, everything is derived from JSON.
    """
    bi = base_intel or {}
    tm = token_meta or {}
    hh = helius_holders or {}

    out = {}

    out["has_mint_authority"] = bool(bi.get("mint_authority"))
    out["has_freeze_authority"] = bool(bi.get("freeze_authority"))

    liq = tm.get("liquidity_usd") if isinstance(tm, dict) else None
    out["has_liquidity"] = liq is not None and liq > 0
    out["liquidity_usd"] = liq

    holders = (hh or {}).get("holders") or []
    percents = [
        h.get("percentage")
        for h in holders
        if h.get("percentage") is not None
    ]

    out["holder_concentration_sample_size"] = len(percents)

    if percents:
        percents_sorted = sorted(percents, reverse=True)
        top1 = percents_sorted[0]
        top5 = sum(percents_sorted[:5]) if len(percents_sorted) >= 5 else sum(percents_sorted)
        top10 = sum(percents_sorted[:10]) if len(percents_sorted) >= 10 else sum(percents_sorted)

        out["top1_pct"] = top1
        out["top5_pct"] = top5
        out["top10_pct"] = top10

        out["is_top1_very_concentrated"] = top1 >= 20.0
        out["is_top10_very_concentrated"] = top10 >= 60.0
    else:
        out["top1_pct"] = None
        out["top5_pct"] = None
        out["top10_pct"] = None
        out["is_top1_very_concentrated"] = None
        out["is_top10_very_concentrated"] = None

    return out

def fetch_solana_holders_html(mint):
    """
    FINAL FALLBACK:
    Scrape SolanaFM holders page instead of Solscan.
    SolanaFM does not block data-center IPs and is stable.
    """
    try:
        url = f"https://solana.fm/token/{mint}"
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, timeout=12, headers=headers).text

        import re

        pattern = r'<a href="/address/([^"]+)".*?</a>.*?<span[^>]*>([\d\.]+)%</span>'
        matches = re.findall(pattern, html, re.S)

        holders = []
        for wallet, pct in matches[:10]:
            holders.append({
                "owner": wallet.strip(),
                "percentage": float(pct),
                "amount_raw": None,
                "amount_ui": None,
            })

        if holders:
            print("HTML FALLBACK: SolanaFM holders extracted.")
            return {
                "mint": mint,
                "holders": holders,
                "source": "solanafm_html"
            }

        print("SolanaFM HTML parsed but no holders found.")
        return {"error": "solanafm_html_empty"}

    except Exception as e:
        print("SolanaFM HTML error:", e)
        return {"error": f"solanafm_html_error: {e}"}

def fetch_dexscreener_token_holders_eth_html(token_address):
    """
    HTML fallback for Dexscreener TOKEN pages.
    Some tokens (NPC etc.) show holders in the UI but *not* in __NEXT_DATA__.
    This extractor scrapes the visible holder table.
    """

    try:
        url = f"https://dexscreener.com/ethereum/{token_address}"
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, timeout=12, headers=headers).text

        import re

        row_pattern = re.compile(
            r"<tr[^>]*>\s*"
            r"<td[^>]*>\s*#?\d+\s*</td>\s*"
            r"<td[^>]*>\s*<a[^>]*?/address/([^\"']+)[^>]*>.*?</a>\s*</td>\s*"
            r"<td[^>]*>\s*([\d\.]+)%\s*</td>",
            re.S
        )

        matches = row_pattern.findall(html)

        holders = []
        for wallet, pct in matches[:10]:
            holders.append({
                "owner": wallet.strip(),
                "amount_ui": None,
                "amount_raw": None,
                "percentage": float(pct),
            })

        if holders:
            return {
                "holders": holders,
                "source": "dexscreener_eth_html"
            }

        return {"error": "dexscreener_html_no_holders"}

    except Exception as e:
        return {"error": f"dexscreener_html_error: {e}"}

def fetch_dexscreener_pair_api_holders(chain, pair_address):
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair_address}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return {"error": "ds_api_status", "code": r.status_code}

        data = r.json() or {}
        pair = data.get("pair") or {}
        holders = pair.get("topHolders") or []

        if not holders:
            return {"error": "ds_api_no_holders"}

        out = []
        for h in holders[:10]:
            out.append({
                "owner": h.get("address"),
                "amount_ui": h.get("balance"),
                "amount_raw": h.get("balance"),
                "percentage": h.get("percentage"),
            })

        return {"holders": out, "source": "dexscreener_api_pair"}

    except Exception as e:
        return {"error": f"ds_api_exception: {e}"}

def fetch_dexscreener_token_holders_eth(token_address):
    """
    Dexscreener TOKEN PAGE holder fetcher for Ethereum.
    This matches how Solana gets holders.
    """
    try:
        url = f"https://dexscreener.com/ethereum/{token_address}"
        html = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"}).text

        import re, json
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.S,
        )
        if not match:
            return {"error": "dexscreener_token_no_json"}

        data = json.loads(match.group(1))

        holders = (
            data.get("props", {})
                .get("pageProps", {})
                .get("token", {})
                .get("topHolders", [])
        )

        if not holders:
            return {"error": "dexscreener_token_no_holders"}

        out = []
        for h in holders[:10]:
            out.append({
                "owner": h.get("address"),
                "amount_ui": h.get("balance"),
                "amount_raw": h.get("balance"),
                "percentage": h.get("percentage"),
            })

        return {"holders": out, "source": "dexscreener_token_eth"}

    except Exception as e:
        return {"error": f"dexscreener_token_error: {e}"}

def fetch_dexscreener_holders(pair_address):
    try:
        url = f"https://dexscreener.com/solana/{pair_address}"
        html = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"}).text

        import re, json

        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if not match:
            return {"error": "dexscreener_scrape_no_json"}

        data = json.loads(match.group(1))

        holders = (
            data.get("props", {})
                .get("pageProps", {})
                .get("pair", {})
                .get("topHolders", [])
        )

        if not holders:
            return {"error": "dexscreener_no_holders"}

        normalized = []
        for h in holders[:10]:
            normalized.append({
                "owner": h.get("address"),
                "amount_ui": h.get("balance"),
                "percentage": h.get("percentage"),
            })

        return {
            "source": "dexscreener_html",
            "holders": normalized,
        }

    except Exception as e:
        return {"error": f"dexscreener_scrape_error: {e}"}

def fetch_ethereum_dexscreener_holders(pair_address):
    """
    Dexscreener holder scraper for ETH pairs.
    Same as Solana version, but chain = ethereum.
    """
    try:
        url = f"https://dexscreener.com/ethereum/{pair_address}"
        html = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"}).text

        import re, json

        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if not match:
            return {"error": "dexscreener_eth_no_json"}

        data = json.loads(match.group(1))

        holders = (
            data.get("props", {})
                .get("pageProps", {})
                .get("pair", {})
                .get("topHolders", [])
        )

        if not holders:
            return {"error": "dexscreener_eth_no_holders"}

        out = []
        for h in holders[:10]:
            out.append({
                "owner": h.get("address"),
                "amount_ui": h.get("balance"),
                "amount_raw": h.get("balance"),
                "percentage": h.get("percentage"),
            })

        return {"holders": out, "source": "dexscreener_eth"}

    except Exception as e:
        return {"error": f"dexscreener_eth_error: {e}"}

def fetch_solana_top_holders(identifier: str, mint: str = None, limit: int = 20):
    """
    Multi-source SPL token holder fetcher.
    Priority:
      0) RPC getTokenLargestAccounts
      1) Helius
      2) Dexscreener HTML
      3) Solscan API
      4) Birdeye distribution
      5) SolanaFM HTML fallback
      6) Empty list fallback
    """

    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [
                mint,
                {
                    "commitment": "finalized",
                    "encoding": "jsonParsed",
                    "withContext": True
                }
            ]
        }

        r = requests.post(SOLANA_RPC_URL, json=payload, timeout=12)
        print("[RPC] getTokenLargestAccounts status:", r.status_code)
        print("[RPC] RAW:", r.text[:600])

        largest_accounts = (r.json() or {}).get("result", {}).get("value", [])
        rpc_holders = []

        for entry in largest_accounts[:limit]:
            token_account = (
                entry.get("address")
                or entry.get("account")
                or entry.get("pubkey")
            )

            ui_amount = (
                entry.get("uiAmount")
                or (float(entry.get("uiAmountString")) if entry.get("uiAmountString") else None)
            )

            raw_amount = entry.get("amount")

            if not token_account:
                continue

            owner_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    token_account,
                    {"encoding": "jsonParsed"}
                ]
            }

            info_res = requests.post(SOLANA_RPC_URL, json=owner_payload, timeout=12)
            acc_info = (info_res.json() or {}).get("result", {}).get("value", {})

            owner = (
                acc_info.get("data", {})
                        .get("parsed", {})
                        .get("info", {})
                        .get("owner")
            )

            if owner:
                rpc_holders.append({
                    "owner": owner,
                    "amount_ui": ui_amount,
                    "amount_raw": raw_amount,
                    "percentage": None
                })

        print("RPC HOLDERS RAW:", rpc_holders)
        print("RPC HOLDERS COUNT:", len(rpc_holders))

        if rpc_holders:

            raw_supply = None
            decimals = None

            try:
                supply_req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenSupply",
                    "params": [mint],
                }
                supply_res = requests.post(SOLANA_RPC_URL, json=supply_req, timeout=12)
                print("[RPC] getTokenSupply status:", supply_res.status_code)
                supply_json = supply_res.json() or {}
                value = (supply_json.get("result") or {}).get("value") or {}

                raw_supply = value.get("amount")
                decimals = value.get("decimals")

                print("RPC SUPPLY RAW:", raw_supply)
                print("RPC DECIMALS:", decimals)

                if raw_supply and decimals is not None:
                    try:
                        supply_int = int(raw_supply)
                        ui_supply = supply_int / (10 ** int(decimals))

                        print("RPC UI SUPPLY:", ui_supply)

                        if ui_supply > 0:
                            for h in rpc_holders:
                                amt_ui = h.get("amount_ui")
                                if amt_ui is not None:
                                    h["percentage"] = (amt_ui / ui_supply) * 100.0
                    except Exception as e:
                        print("RPC percentage calc error (math):", e)

            except Exception as e:
                print("RPC percentage calc error (request):", e)

            print("RPC HOLDERS WITH PERCENT:", rpc_holders)

            return {
                "mint": mint,
                "holders": rpc_holders,
                "decimals": decimals,
                "total_supply_raw": raw_supply,
                "source": "rpc_largest_accounts",
            }

    except Exception as e:
        print("[RPC HOLDERS ERROR]:", e)

    if HELIUS_API_KEY and mint:
        try:
            url = "https://api.helius.xyz/v0/tokens/holders"
            params = {"api-key": HELIUS_API_KEY, "mint": mint, "page": 1, "pageSize": limit}

            r = requests.get(url, params=params, timeout=12)
            print("[HELIUS] HOLDERS status:", r.status_code)
            print("[HELIUS] RAW:", r.text[:600])

            if r.status_code == 200:
                data = r.json() or {}
                holders = data.get("holders") or []

                if not holders:
                    print("[HELIUS] holders list empty, falling back to other providers.")
                else:
                    decimals = data.get("decimals")
                    total_supply_raw = data.get("totalSupply")

                    supply_int = None
                    try:
                        if total_supply_raw:
                            supply_int = int(total_supply_raw)
                    except Exception:
                        supply_int = None

                    out_list = []
                    for h in holders:
                        owner = h.get("owner")
                        amt_raw = h.get("amount")
                        if not owner or amt_raw is None:
                            continue

                        percent = None
                        amt_ui = None
                        try:
                            amt_int = int(amt_raw)
                            if decimals is not None:
                                amt_ui = amt_int / (10 ** int(decimals))
                            if supply_int:
                                percent = (amt_int / supply_int) * 100.0
                        except Exception:
                            pass

                        out_list.append({
                            "owner": owner,
                            "amount_raw": amt_raw,
                            "amount_ui": amt_ui,
                            "percentage": percent,
                        })

                    if out_list:
                        return {
                            "mint": mint,
                            "holders": out_list,
                            "decimals": decimals,
                            "total_supply_raw": total_supply_raw,
                            "source": "helius"
                        }

        except Exception as e:
            print("[HELIUS ERROR]:", e)

    if identifier and len(identifier) < 44:
        try:
            ds = fetch_dexscreener_holders(identifier)
            if ds and isinstance(ds, dict) and ds.get("holders"):
                print("[DEXSCREENER HTML] holders found")
                return {
                    "mint": mint,
                    "holders": ds.get("holders"),
                    "decimals": None,
                    "total_supply_raw": None,
                    "source": "dexscreener_html",
                }
        except Exception as e:
            print("[DEXSCREENER HTML ERROR]:", e)

    try:
        url = f"https://api.solscan.io/token/holders?token={mint}&offset=0&size={limit}"
        r = requests.get(url, timeout=12)
        print("[SOLSCAN] status:", r.status_code)
        print("[SOLSCAN] RAW:", r.text[:600])

        if r.status_code == 200:
            payload = r.json() or {}
            arr = payload.get("data") or []
            out_list = []

            for h in arr:
                owner = h.get("address")
                amount = h.get("amount")
                out_list.append({
                    "owner": owner,
                    "amount_raw": str(amount),
                    "amount_ui": None,
                    "percentage": None
                })

            if out_list:
                return {
                    "mint": mint,
                    "holders": out_list,
                    "decimals": None,
                    "total_supply_raw": None,
                    "source": "solscan"
                }

    except Exception as e:
        print("[SOLSCAN ERROR]:", e)

    if BIRDEYE_API_KEY:
        try:
            url = f"https://public-api.birdeye.so/public/token/holder-distribution/{mint}"
            headers = {"X-API-KEY": BIRDEYE_API_KEY, "accept": "application/json"}
            r = requests.get(url, headers=headers, timeout=12)
            print("[BIRDEYE HOLDERS] status:", r.status_code)
            print("[BIRDEYE HOLDERS RAW]:", r.text[:600])

            if r.status_code == 200:
                data = (r.json() or {}).get("data", {})
                arr = data.get("topHolders") or []
                out_list = []

                for h in arr:
                    out_list.append({
                        "owner": h.get("address"),
                        "amount_raw": str(h.get("balance")),
                        "amount_ui": None,
                        "percentage": h.get("percentage")
                    })

                if out_list:
                    return {
                        "mint": mint,
                        "holders": out_list,
                        "decimals": None,
                        "total_supply_raw": None,
                        "source": "birdeye"
                    }

        except Exception as e:
            print("[BIRDEYE HOLDER ERROR]:", e)

    html_fallback = fetch_solana_holders_html(mint)
    if html_fallback and html_fallback.get("holders"):
        print("SolanaFM HTML fallback succeeded, holders recovered.")
        return html_fallback

    print("No Solana holder data available from any provider. Returning empty list.")
    return {
        "mint": mint,
        "holders": [],
        "decimals": None,
        "total_supply_raw": None,
        "error": "No holder data available from any provider",
        "source": "none"
    }

def derive_eth_risk_hints(intel: dict) -> dict:
    """
    Build simple, explicit risk hints from ABI + metadata so the LLM
    can reason about them without inventing.
    """
    abi_functions = intel.get("abi_functions") or []
    fn_lower = [f.lower() for f in abi_functions]

    def has_any(names):
        return any(n in fn_lower for n in names)

    risk = {
        "has_owner_like": has_any(["owner"]),
        "has_transfer_ownership": has_any(["transferownership", "transfer_ownership"]),
        "has_mint": has_any(["mint"]),
        "has_pausing": has_any(["pause", "unpause", "setpaused"]),
        "has_blacklist": has_any(["blacklist", "setblacklist", "addblacklist"]),
        "has_whitelist": has_any(["whitelist"]),
        "has_withdraw": has_any(["withdraw", "sweep", "claim"]),
        "has_fee_change": has_any(["setfee", "setfees", "updatefee"]),
        "is_proxy": bool(intel.get("proxy")),
        "has_implementation": bool(intel.get("implementation")),
        "is_verified": bool(intel.get("verified")),
        "function_count": intel.get("abi_function_count") or 0,
    }
    return risk

def fetch_market_data_dexscreener(address: str):
    """
    Improved Dexscreener discovery:
    1. Try /tokens/<address>
    2. If no pairs: try /search?q=<address>
    3. If still nothing: try /pairs/solana/<address>
    Returns a normalized dict with Dexscreener-style keys.
    """

    base = "https://api.dexscreener.com/latest/dex"
    best = None

    try:
        r = requests.get(f"{base}/tokens/{address}", timeout=10)
        print("[DEXSCREENER /tokens] status:", r.status_code)
        if r.status_code == 200:
            data = r.json() or {}
            pairs = data.get("pairs") or []
            if pairs:
                best = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd", 0) or 0)
                print("Dexscreener pair found via /tokens/")
    except Exception as e:
        print("[DEXSCREENER /tokens] error:", e)

    if best is None:
        try:
            r = requests.get(f"{base}/search?q={address}", timeout=10)
            print("[DEXSCREENER /search] status:", r.status_code)
            if r.status_code == 200:
                data = r.json() or {}
                pairs = data.get("pairs") or []
                if pairs:
                    best = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd", 0) or 0)
                    print("Dexscreener pair found via /search/")
        except Exception as e:
            print("[DEXSCREENER /search] error:", e)

    if best is None:
        try:
            r = requests.get(f"{base}/pairs/solana/{address}", timeout=10)
            print("[DEXSCREENER /pairs] status:", r.status_code)
            if r.status_code == 200:
                data = (r.json() or {}).get("pair")
                if data:
                    best = data
                    print("Dexscreener direct pair match")
        except Exception as e:
            print("[DEXSCREENER /pairs] error:", e)

    if not best:
        print("No market data found from Dexscreener for:", address)
        return {"error": "No market data found"}

    liquidity = best.get("liquidity") or {}
    volume = best.get("volume") or {}
    base_token = best.get("baseToken") or {}

    return {
        "baseToken": base_token,
        "token_name": base_token.get("name"),
        "symbol": base_token.get("symbol"),
        "price_usd": best.get("priceUsd"),
        "fdv": best.get("fdv"),
        "market_cap": best.get("marketCap"),
        "liquidity_usd": liquidity.get("usd"),
        "volume_24h": volume.get("h24"),
        "pair_address": best.get("pairAddress"),
        "pair_url": best.get("url"),
        "dex_name": best.get("dexId"),
        "chain": best.get("chainId"),
        "top_holders": best.get("topHolders"),
    }

def fetch_top_erc20_holders(address: str, token_meta=None, total_supply_raw=None, limit: int = 10, pair_address=None):
    """
    Multi-source Ethereum holder fetcher (CORRECT PRIORITY):

        1) Dexscreener token-page JSON (primary, most accurate)
        2) Dexscreener API topHolders (if available)
        3) Etherscan holderlist API
        4) Honeypot holder summary fallback
        5) EMPTY fallback

    NORMALIZED OUTPUT FORMAT (matches Solana):
        {
          "holders": [
            {
              "owner": "0x....",
              "amount_ui": <float or None>,
              "amount_raw": <str or None>,
              "percentage": <float or None>
            },
            ...
          ],
          "source": "etherscan" | "dexscreener_eth" | "honeypot_summary" | "none",
          "error": "..."   # optional
        }
    """

    base = token_meta.get("baseToken") if isinstance(token_meta, dict) else None
    base = base or (token_meta.get("base_token") if isinstance(token_meta, dict) else None)

    token_addr = base.get("address") if isinstance(base, dict) else address

    ds_token = fetch_dexscreener_token_holders_eth(token_addr)
    if ds_token.get("holders"):
        return ds_token

    ds_html = fetch_dexscreener_token_holders_eth_html(token_addr)
    if ds_html.get("holders"):
        return ds_html

    if pair_address:
        ds_api = fetch_dexscreener_pair_api_holders("ethereum", pair_address)
        if ds_api.get("holders"):
            return ds_api

    if token_meta and token_meta.get("top_holders"):
        out = []
        for h in token_meta["top_holders"][:10]:
            out.append({
                "owner": h.get("address"),
                "amount_ui": h.get("balance"),
                "amount_raw": h.get("balance"),
                "percentage": h.get("percentage"),
            })
        return {"holders": out, "source": "dexscreener_api"}

    if ETHERSCAN_API_KEY:
        try:
            url = "https://api.etherscan.io/api"
            params = {
                "module": "token",
                "action": "tokenholderlist",
                "contractaddress": address,
                "page": 1,
                "offset": limit,
                "apikey": ETHERSCAN_API_KEY,
            }

            r = requests.get(url, params=params, timeout=15)
            data = r.json()

            if data.get("status") == "1":
                result = data.get("result") or []
                holders_out = []

                supply_int = None
                if total_supply_raw:
                    try:
                        supply_int = int(total_supply_raw)
                    except Exception:
                        supply_int = None

                for row in result[:limit]:
                    addr = row.get("TokenHolderAddress")
                    qty_str = row.get("TokenHolderQuantity")

                    percent = None
                    if supply_int and qty_str is not None:
                        try:
                            percent = (int(qty_str) / supply_int) * 100.0
                        except Exception:
                            pass

                    holders_out.append({
                        "owner": addr,
                        "amount_ui": None,
                        "amount_raw": qty_str,
                        "percentage": percent,
                    })

                if holders_out:
                    return {"holders": holders_out, "source": "etherscan"}

        except Exception as e:
            print("ETHERSCAN HOLDER ERROR:", e)

    try:
        hp = fetch_honeypot_analysis(address, chain_id=1)
        ha = (hp or {}).get("holder_analysis") or {}

        if ha.get("holders_analyzed"):
            
            return {
                "holders": [],
                "source": "honeypot_summary",
                "error": "detailed_holder_data_unavailable"
            }

    except Exception as e:
        print("HONEYPOT HOLDER FALLBACK ERROR:", e)


    return {
        "holders": [],
        "error": "No holder data available from any provider",
        "source": "none",
    }

def safe_request(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None

def extract_section(html, keywords):
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    desc = soup.find("div", {"data-role": "coin-description"})
    if desc:
        clean = desc.get_text(" ", strip=True)
        if _is_valid_description(clean):
            return clean

    old_desc = soup.select_one("div.coin-description, div.sc-16891c57-0")
    if old_desc:
        clean = old_desc.get_text(" ", strip=True)
        if _is_valid_description(clean):
            return clean

    ds_desc = soup.select_one("div.description, p.description")
    if ds_desc:
        clean = ds_desc.get_text(" ", strip=True)
        if _is_valid_description(clean):
            return clean

    text = soup.get_text("\n")
    lower = text.lower()

    for kw in keywords:
        idx = lower.find(kw.lower())
        if idx != -1:
            start = max(0, idx - 300)
            end = min(len(text), idx + 300)
            block = text[start:end].strip()

            bad_markers = [
                "verifying you are human",
                "enable javascript",
                "enable cookies",
                "cloudflare",
                "challenge verification",
                "captcha",
                "cookie",
                "consent",
            ]
            bl = block.lower()
            if any(m in bl for m in bad_markers):
                return None

            if _looks_like_cmc_menu(block):
                return None

            if _is_valid_description(block):
                return block

    return None

def _looks_like_cmc_menu(text):
    """Detect CMC navigation/menu garbage instead of token description."""
    garbage_keywords = [
        "cryptocurrencies", "ranking", "categories", "historical snapshots",
        "gainers & losers", "most visited", "fear and greed index",
        "market overview", "derivatives market", "spot market",
        "upcoming sales", "nft stats", "leaders", "dashboards",
    ]
    t = text.lower()
    return any(g in t for g in garbage_keywords)

def _is_valid_description(text):
    """Reject text that is too short or looks like site UI instead of real summary."""
    if not text:
        return False
    if len(text) < 40:
        return False
    if _looks_like_cmc_menu(text):
        return False
    return True


def fetch_honeypot_analysis(address, chain_id=None):
    """
    Wraps Honeypot.is /v2/IsHoneypot

    Returns a SMALL, stable dict for the LLM:
      - summary.risk / summary.riskLevel
      - honeypotResult.isHoneypot / honeypotReason
      - simulationResult.{buyTax,sellTax,transferTax}
      - holderAnalysis (aggregates only, not full holders)
      - key liquidity info from pair/liquidity
      - flags (summary.flags if present, or legacy flags as fallback)
    """
    base_url = "https://api.honeypot.is/v2/IsHoneypot"

    params = {"address": address}
    if chain_id is not None:
        params["chainID"] = chain_id

    headers = {}
    if HONEYPOT_API_KEY:
        headers["X-API-KEY"] = HONEYPOT_API_KEY

    try:
        r = requests.get(base_url, params=params, headers=headers, timeout=15)
        print("[HONEYPOT] status:", r.status_code)
        print("[HONEYPOT] raw:", r.text[:600])

        if r.status_code == 404:
            return {"error": "Token not found on Honeypot.is"}

        r.raise_for_status()
        data = r.json()

        token = data.get("token") or {}
        summary = data.get("summary") or {}
        honeypot_result = data.get("honeypotResult") or {}
        sim = data.get("simulationResult") or {}
        holder_analysis = data.get("holderAnalysis") or {}
        pair = data.get("pair") or {}
        pair_inner = pair.get("pair") or {}

        summary_flags = summary.get("flags") or []
        legacy_flags = data.get("flags") or []
        combined_flags = summary_flags or legacy_flags

        out = {
            "token_name": token.get("name"),
            "token_symbol": token.get("symbol"),
            "token_decimals": token.get("decimals"),
            "token_total_holders": token.get("totalHolders"),

            "summary_risk": summary.get("risk"),
            "summary_risk_level": summary.get("riskLevel"),

            "is_honeypot": honeypot_result.get("isHoneypot"),
            "honeypot_reason": honeypot_result.get("honeypotReason"),

            "buy_tax": sim.get("buyTax"),
            "sell_tax": sim.get("sellTax"),
            "transfer_tax": sim.get("transferTax"),
            "buy_gas": sim.get("buyGas"),
            "sell_gas": sim.get("sellGas"),

            "holder_analysis": {
                "holders_analyzed": holder_analysis.get("holders"),
                "successful": holder_analysis.get("successful"),
                "failed": holder_analysis.get("failed"),
                "siphoned": holder_analysis.get("siphoned"),
                "average_tax": holder_analysis.get("averageTax"),
                "average_gas": holder_analysis.get("averageGas"),
                "highest_tax": holder_analysis.get("highestTax"),
                "high_tax_wallets": holder_analysis.get("highTaxWallets"),
            },

            "pair": {
                "name": pair_inner.get("name"),
                "address": pair_inner.get("address"),
                "type": pair_inner.get("type"),
                "liquidity_usd": pair.get("liquidity"),
                "router": pair.get("router"),
                "chain_id": pair.get("chainId"),
            },

            "flags": combined_flags,
        }

        return out

    except Exception as e:
        print("[HONEYPOT] error:", e)
        return {"error": f"Honeypot API error: {e}"}
        
def fetch_etherscan_contract_intel(address: str):
    """
    Fetch Ethereum contract metadata using Etherscan V2 API.
    Includes ABI so we can run exploit-surface and admin-risk analysis.
    """
    if not ETHERSCAN_API_KEY:
        return {"error": "ETHERSCAN_API_KEY not configured"}

    try:
        base = "https://api.etherscan.io/v2/api"

        params_meta = {
            "module": "contract",
            "action": "getsourcecode",
            "address": address,
            "apikey": ETHERSCAN_API_KEY,
            "chainid": 1,
        }
        print("[ETHERSCAN] META (V2) request:", params_meta)

        r_meta = requests.get(base, params=params_meta, timeout=15)
        print("[ETHERSCAN] META status:", r_meta.status_code)

        r_meta.raise_for_status()
        meta_res = r_meta.json()
        print("[ETHERSCAN] META response:", meta_res)

        if meta_res.get("status") != "1":
            return {"error": f"Etherscan V2 META error: {meta_res}"}

        meta_list = meta_res.get("result") or [{}]
        meta = meta_list[0]

        total_supply = None
        params_supply = {
            "module": "stats",
            "action": "tokensupply",
            "contractaddress": address,
            "apikey": ETHERSCAN_API_KEY,
            "chainid": 1,
        }
        print("[ETHERSCAN] SUPPLY (V2) request:", params_supply)

        r_sup = requests.get(base, params=params_supply, timeout=15)
        print("[ETHERSCAN] SUPPLY status:", r_sup.status_code)

        r_sup.raise_for_status()
        sup_res = r_sup.json()
        print("[ETHERSCAN] SUPPLY response:", sup_res)

        if sup_res.get("status") == "1":
            total_supply = sup_res.get("result")

        abi_text = meta.get("ABI") or "[]"
        try:
            abi = json.loads(abi_text)
        except Exception:
            abi = []

        fn_signatures = [
            f.get("name")
            for f in abi
            if isinstance(f, dict) and f.get("type") == "function"
        ]

        intel = {
            "contract_name": meta.get("ContractName"),
            "compiler_version": meta.get("CompilerVersion"),
            "optimization_used": meta.get("OptimizationUsed"),
            "runs": meta.get("Runs"),
            "evm_version": meta.get("EVMVersion"),
            "license_type": meta.get("LicenseType"),
            "verified": meta.get("IsVerified") == "1",
            "implementation": meta.get("Implementation"),
            "proxy": meta.get("Proxy") == "1",
            "total_supply_raw": total_supply,
            "abi_function_count": len(fn_signatures),
            "abi_functions": fn_signatures[:80],
            "abi": abi,
        }

        intel["risk_hints"] = derive_eth_risk_hints(intel)
        return intel

    except Exception as e:
        print("[ETHERSCAN V2] Exception:", e)
        return {"error": f"Etherscan V2 error: {e}"}

def analyze_abi_exploits(abi: list) -> dict:
    """
    Very lightweight static "exploit surface" scan based on ABI function names.

    We DO NOT claim to detect real exploits here.
    We only flag dangerous-looking capabilities so the LLM can reason about them.
    """
    if not isinstance(abi, list):
        return {"error": "no_abi"}

    dangerous_names = []
    patterns = {
        "mint": ["mint"],
        "burn": ["burn"],
        "withdraw": ["withdraw", "sweep", "drain"],
        "owner_change": ["transferownership", "setowner", "changeowner", "setadmin"],
        "fee_change": ["setfee", "setfees", "updatefee", "settax", "settaxes"],
        "pause": ["pause", "unpause", "setpaused"],
        "blacklist": ["blacklist", "setblacklist", "addblacklist"],
        "whitelist": ["whitelist"],
        "upgrade": ["upgrade", "upgradeTo", "setimplementation", "setImplementation"],
        "raw_call": ["call", "delegatecall"],
    }

    lower_name_map = []

    for item in abi:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function":
            continue
        name = item.get("name")
        if not name:
            continue
        ln = name.lower()
        lower_name_map.append((name, ln))

    flags = {k: False for k in patterns.keys()}

    for original, ln in lower_name_map:
        for key, substrs in patterns.items():
            if any(sub.lower() in ln for sub in substrs):
                flags[key] = True
                dangerous_names.append(original)

    return {
        "dangerous_functions": list(sorted(set(dangerous_names))),
        "flags": flags,
        "function_count": len(lower_name_map),
    }


def classify_admin_risk(fn_names):
    """
    Heuristic admin-control classifier based purely on function names.
    This is NOT a real audit, just signal for the LLM.
    """
    if not fn_names:
        return {
            "admin_control_level": "Unknown",
            "reason": "No ABI function names available.",
            "signals": [],
        }

    fn_lower = [f.lower() for f in fn_names]

    def has_any(subs):
        return any(s in fn for fn in fn_lower for s in subs)

    high_signals = []
    medium_signals = []

    if has_any(["transferownership", "setowner", "changeowner", "setadmin"]):
        high_signals.append("Owner/admin can be reassigned.")
    if has_any(["mint"]):
        high_signals.append("Contract exposes mint capabilities.")
    if has_any(["upgrade", "setimplementation"]):
        high_signals.append("Proxy or upgrade-type functions present.")
    if has_any(["pause", "unpause", "setpaused"]):
        medium_signals.append("Contract can be paused/unpaused.")
    if has_any(["blacklist", "whitelist"]):
        medium_signals.append("Blacklist/whitelist mechanics present.")
    if has_any(["setfee", "setfees", "updatefee", "settax"]):
        medium_signals.append("Admin can change fee/tax parameters.")
    if has_any(["withdraw", "sweep", "drain"]):
        high_signals.append("Admin has withdraw/sweep-style functions.")

    if high_signals:
        level = "High"
    elif medium_signals:
        level = "Moderate"
    else:
        level = "Low"

    all_signals = high_signals + medium_signals

    return {
        "admin_control_level": level,
        "signals": all_signals,
        "has_high_risk_capabilities": bool(high_signals),
        "has_medium_risk_capabilities": bool(medium_signals),
    }

def build_signal_indicators(
    base_intel,
    token_meta,
    honeypot,
    risk_hints,
    admin_risk,
):
    """
    Build a simple + / - indicator set so the LLM can echo them cleanly.

    Returns:
      {
        "positives": ["...", "..."],
        "negatives": ["...", "..."]
      }
    """
    positives = []
    negatives = []

    bi = base_intel or {}
    tm = token_meta or {}
    hp = honeypot or {}
    rh = risk_hints or {}
    ar = admin_risk or {}

    if bi.get("verified") is True:
        positives.append("Contract source is verified on Etherscan.")
    elif bi.get("verified") is False:
        negatives.append("Contract source is not verified on Etherscan.")

    if bi.get("proxy") and bi.get("implementation"):
        negatives.append("Proxy pattern with separate implementation; contract is upgradeable.")
    elif bi.get("proxy"):
        negatives.append("Proxy contract detected; upgradeability surface exists.")

    if tm.get("liquidity_usd") not in (None, 0):
        positives.append("Dexscreener shows non-zero liquidity for this token.")
    else:
        negatives.append("No Dexscreener liquidity information or pair found.")

    hp_risk = hp.get("summary_risk_level")
    if hp_risk:
        if isinstance(hp_risk, str) and hp_risk.lower() in ["high", "very high", "critical"]:
            negatives.append(f"Honeypot.is risk level reported as {hp_risk}.")
        elif isinstance(hp_risk, str) and hp_risk.lower() in ["low", "very low"]:
            positives.append(f"Honeypot.is risk level reported as {hp_risk}.")
    if hp.get("is_honeypot") is True:
        negatives.append("Honeypot.is flags this token as a honeypot.")
    if hp.get("buy_tax") is not None or hp.get("sell_tax") is not None:
        positives.append("Tax information available from Honeypot.is simulation.")

    if rh.get("has_mint_authority"):
        negatives.append("Mint authority exists; supply can potentially be expanded.")
    if rh.get("has_freeze_authority"):
        negatives.append("Freeze authority exists; accounts can potentially be frozen.")

    top10 = rh.get("top10_pct")
    conc1 = rh.get("is_top1_very_concentrated")
    conc10 = rh.get("is_top10_very_concentrated")

    try:
        if conc1 is True or conc10 is True:
            negatives.append("Solana holder set appears highly concentrated in a few wallets.")
        elif (isinstance(top10, (int, float)) and top10 <= 30.0):
            positives.append("Solana top holders do not appear extremely concentrated based on available sample.")
    except Exception:

        pass

    level = ar.get("admin_control_level")
    if level == "High":
        negatives.append("Admin control level classified as High based on ABI function names.")
    elif level == "Moderate":
        negatives.append("Admin control level classified as Moderate based on ABI function names.")
    elif level == "Low":
        positives.append("Admin control level classified as Low based on ABI function names.")

    return {
        "positives": positives,
        "negatives": negatives,
    }

def detect_lp_lock_status(network, token_meta):
    out = {
        "status": "unknown",
        "lock_provider": None,
        "lock_expires": None,
        "details": None,
    }

    if not token_meta or not isinstance(token_meta, dict):
        out["details"] = "No token metadata available"
        return out

    pair_address = token_meta.get("pair_address")
    if not pair_address:
        out["details"] = "No pair address present for LP lock detection"
        return out

    if network == "ethereum":
        ds_lp = fetch_ethereum_dexscreener_holders(pair_address)
        if ds_lp.get("holders"):
            holders = ds_lp
        else:
            holders = fetch_top_erc20_holders(
                address=pair_address,
                total_supply_raw=None,
                limit=10,
                pair_address=None
            )

        for h in holders.get("holders", []):
            if (h.get("owner") or "").lower() == "0x000000000000000000000000000000000000dead":
                out["status"] = "burned"
                out["details"] = "LP sent to burn address"
                return out

        lockers = [
            "0x55d398",
            "0x4f0f9a",
            "0x71b575"
        ]

        for h in holders.get("holders", []):
            addr = (h.get("owner") or "").lower()
            if any(addr.startswith(p[:6]) for p in lockers):
                out["status"] = "locked"
                out["details"] = "LP held in known lock vault"
                return out

        out["status"] = "unlocked"
        out["details"] = "LP appears held by regular wallet(s)"
        return out

    if network == "solana":
        info = fetch_solana_account_info(pair_address)
        if info.get("error"):
            out["details"] = "Could not fetch Solana LP mint"
            return out

        if info.get("owner_program") == "11111111111111111111111111111111":
            out["status"] = "burned"
            out["details"] = "LP mint burned"
            return out

        if info.get("owner_program") == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb":
            out["status"] = "locked"
            out["lock_provider"] = "pump.fun"
            out["details"] = "LP locked in pump.fun vault"
            return out

        out["status"] = "unlocked"
        out["details"] = "LP not burned or locked"
        return out

    return out

def _build_clusters_from_edges(edges):
    """
    edges: list of (src, dst)
    Returns { "nodes": set, "clusters": [set, ...] }
    """
    graph = {}
    for a, b in edges:
        if a == b:
            continue
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)

    visited = set()
    clusters = []

    for node in graph.keys():
        if node in visited:
            continue
        stack = [node]
        comp = set()
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            comp.add(cur)
            for nxt in graph.get(cur, []):
                if nxt not in visited:
                    stack.append(nxt)
        clusters.append(comp)

    nodes = set(graph.keys())
    return {"nodes": nodes, "clusters": clusters}


def _summarize_bubblemap_graph(clusters, interesting_addrs):
    """
    Build a small, LLM-friendly summary from cluster sets.
    """
    cluster_objs = []
    suspicious_count = 0

    for i, comp in enumerate(clusters):
        comp_list = sorted(list(comp))
        size = len(comp_list)
        in_interest = [a for a in comp_list if a in interesting_addrs]

        suspicious = False
        reason = None

        if size >= 3 and len(in_interest) >= 2:
            suspicious = True
            suspicious_count += 1
            reason = "multiple top holders or key addresses clustered together"

        cluster_objs.append({
            "id": i,
            "size": size,
            "addresses": comp_list,
            "interesting_addresses": in_interest,
            "suspicious": suspicious,
            "suspicious_reason": reason,
        })

    largest = max((len(c) for c in clusters), default=0)

    return {
        "cluster_count": len(clusters),
        "largest_cluster_size": largest,
        "suspicious_cluster_count": suspicious_count,
        "clusters": cluster_objs,
    }


def _build_eth_bubblemap(contract_address, top_holders):
    """
    Cheap, bounded EVM "bubblemap" using Etherscan token transfer history.

    - Only looks at a capped number of transfers
    - Only keeps edges between the contract + top holders
    """
    if not ETHERSCAN_API_KEY:
        return {"error": "ETHERSCAN_API_KEY not configured for bubblemap"}

    holder_list = (top_holders or {}).get("holders") or []
    holder_addrs = {h.get("owner", "").lower() for h in holder_list if h.get("owner")}
    holder_addrs = {a for a in holder_addrs if a}

    if not holder_addrs:
        return {"error": "no_top_holders_for_eth_bubblemap"}

    interesting = set(holder_addrs)
    contract_lower = (contract_address or "").lower()
    if contract_lower:
        interesting.add(contract_lower)

    url = "https://api.etherscan.io/api"
    params = {
        "module": "account",
        "action": "tokentx",
        "contractaddress": contract_address,
        "page": 1,
        "offset": BUBBLEMAP_MAX_ETH_TX,
        "sort": "asc",
        "apikey": ETHERSCAN_API_KEY,
    }

    edges = []
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("status") != "1":
            return {"error": f"etherscan_tokentx_status_{data.get('status')}"}

        for tx in data.get("result", []):
            from_addr = (tx.get("from") or "").lower()
            to_addr = (tx.get("to") or "").lower()
            if not from_addr or not to_addr:
                continue
            # Only edges between interesting nodes
            if from_addr in interesting or to_addr in interesting:
                edges.append((from_addr, to_addr))

    except Exception as e:
        return {"error": f"eth_bubblemap_request_error: {e}"}

    if not edges:
        return {"error": "no_edges_after_filtering"}

    cluster_data = _build_clusters_from_edges(edges)
    summary = _summarize_bubblemap_graph(cluster_data["clusters"], interesting)

    return {
        "network": "ethereum",
        "contract_address": contract_address,
        "node_count": len(cluster_data["nodes"]),
        "edge_count": len(edges),
        "summary": summary,
    }


def _build_solana_bubblemap(mint, sol_top_holders):
    """
    Cheap, bounded SPL "bubblemap" using Helius enhanced transactions.

    We:
      - take the top N holder wallets
      - pull a capped number of transactions per wallet
      - only keep tokenTransfers involving this mint
      - connect holders that send to / receive from each other

    Everything is heavily capped to avoid surprise costs.
    """
    if not HELIUS_API_KEY:
        return {"error": "HELIUS_API_KEY not configured for bubblemap"}

    holders = (sol_top_holders or {}).get("holders") or []
    holders = holders[:BUBBLEMAP_MAX_HOLDERS]

    wallet_addrs = {h.get("owner") for h in holders if h.get("owner")}
    wallet_addrs = {a for a in wallet_addrs if a}

    if not wallet_addrs:
        return {"error": "no_solana_holders_for_bubblemap"}

    base = "https://api-mainnet.helius-rpc.com/v0/addresses"
    edges = []
    interesting = set(wallet_addrs)

    try:
        for w in wallet_addrs:
            url = f"{base}/{w}/transactions"
            params = {
                "api-key": HELIUS_API_KEY,
                "limit": BUBBLEMAP_MAX_SOL_TX,
            }
            r = requests.get(url, params=params, timeout=15)
            if r.status_code != 200:
                continue

            txs = r.json() or []
            for tx in txs:
                for t in tx.get("tokenTransfers", []) or []:
                    if t.get("mint") != mint:
                        continue
                    src = t.get("fromUserAccount")
                    dst = t.get("toUserAccount")
                    if not src or not dst:
                        continue
                    if (src in interesting) or (dst in interesting):
                        edges.append((src, dst))

    except Exception as e:
        return {"error": f"solana_bubblemap_request_error: {e}"}

    if not edges:
        return {"error": "no_edges_after_filtering"}

    cluster_data = _build_clusters_from_edges(edges)
    summary = _summarize_bubblemap_graph(cluster_data["clusters"], interesting)

    return {
        "network": "solana",
        "mint": mint,
        "node_count": len(cluster_data["nodes"]),
        "edge_count": len(edges),
        "summary": summary,
    }


def build_bubblemap_analysis(network, contract_or_mint, top_holders, sol_top_holders):
    """
    High-level entry point used by process_contract_intel.

    network: "solana" | "ethereum"
    contract_or_mint: string
    top_holders: Ethereum holder dict (or None)
    sol_top_holders: Solana holder dict (or None)
    """
    net = (network or "").lower().strip()

    if BUBBLEMAP_MODE == "off":
        return None

    if net == "ethereum":
        if BUBBLEMAP_MODE not in ("both", "eth"):
            return None
        return _build_eth_bubblemap(contract_or_mint, top_holders)

    if net == "solana":
        if BUBBLEMAP_MODE not in ("both", "sol"):
            return None
        return _build_solana_bubblemap(contract_or_mint, sol_top_holders)

    return None

@celery.task(name="process_risk_engine")
def process_risk_engine(
    asset_id,
    runs,
    steps,
    mu,
    sigma,
    start_price,
    seed,
    out_format,
    wallet
):
    """
    Monte Carlo Risk Simulation Engine
    Runs GBM simulation and exports structured report.
    """

    import math
    import random

    if seed is not None:
        random.seed(seed)    

    if not isinstance(steps, int) or steps <= 0:
        raise ValueError("steps must be a positive integer")

    if not isinstance(runs, int) or runs <= 0:
        raise ValueError("runs must be a positive integer")

    if float(start_price) <= 0:
        raise ValueError("start_price must be > 0")

    dt = 1.0 / float(steps)
    paths = []

    for _ in range(runs):
        price = float(start_price)
        path = [price]

        for _ in range(steps):
            z = random.gauss(0, 1)
            price *= math.exp((mu - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z)
            path.append(price)

        paths.append(path)

    final_prices = [p[-1] for p in paths]

    avg_final = sum(final_prices) / len(final_prices)
    min_final = min(final_prices)
    max_final = max(final_prices)

    returns = [((p - float(start_price)) / float(start_price)) for p in final_prices]

    avg_return = sum(returns) / len(returns)
    min_return = min(returns)
    max_return = max(returns)

    sorted_prices = sorted(final_prices)

    def percentile(data, pct):
        if not data:
            return None
        if len(data) == 1:
            return data[0]

        k = (len(data) - 1) * pct
        f = int(k)
        c = min(f + 1, len(data) - 1)

        if f == c:
            return data[f]

        d0 = data[f] * (c - k)
        d1 = data[c] * (k - f)
        return d0 + d1

    p5 = percentile(sorted_prices, 0.05)
    p25 = percentile(sorted_prices, 0.25)
    p50 = percentile(sorted_prices, 0.50)
    p75 = percentile(sorted_prices, 0.75)
    p95 = percentile(sorted_prices, 0.95)

    # Sample Path Chart
    sample_chart_path = f"/tmp/{asset_id}_paths.png"
    sample_paths = paths[:20]

    plt.figure(figsize=(6,4))
    for p in sample_paths:
        plt.plot(p)
    plt.title("Sample Price Paths")
    plt.xlabel("Time Steps")
    plt.ylabel("Price")
    plt.tight_layout()
    plt.savefig(sample_chart_path)
    plt.close()

    # Final Price Distribution Chart
    distribution_chart_path = f"/tmp/{asset_id}_distribution.png"

    plt.figure(figsize=(6,4))
    plt.hist(final_prices, bins=40)
    plt.axvline(p5, linestyle="dashed", linewidth=1)
    plt.axvline(p50, linestyle="dashed", linewidth=1)
    plt.axvline(p95, linestyle="dashed", linewidth=1)
    plt.title("Final Price Distribution")
    plt.xlabel("Final Price")
    plt.ylabel("Frequency")
    plt.tight_layout()
    plt.savefig(distribution_chart_path)
    plt.close()

    # Combine both charts vertically
    combined_chart_path = f"/tmp/{asset_id}_combined.png"

    img1 = Image.open(sample_chart_path).convert("RGB")
    img2 = Image.open(distribution_chart_path).convert("RGB")

    width = max(img1.width, img2.width)
    height = img1.height + img2.height

    combined = Image.new("RGB", (width, height), "white")
    combined.paste(img1, (0, 0))
    combined.paste(img2, (0, img1.height))
    combined.save(combined_chart_path)

    chart_path = combined_chart_path

    prob_loss = sum(1 for p in final_prices if p < float(start_price)) / len(final_prices)
    prob_loss_20 = sum(1 for p in final_prices if p < float(start_price) * 0.8) / len(final_prices)
    prob_gain_50 = sum(1 for p in final_prices if p > float(start_price) * 1.5) / len(final_prices)

    if prob_loss > 0.60:
        risk_profile = "High downside risk based on the share of simulations finishing below the starting price."
    elif prob_loss > 0.40:
        risk_profile = "Moderate downside risk with a fairly balanced distribution of positive and negative outcomes."
    else:
        risk_profile = "Lower downside risk based on this simulation sample."

    if max_return > 1.5:
        upside_profile = "The simulation also shows a large upside tail, meaning some paths end materially above the starting value."
    elif max_return > 0.5:
        upside_profile = "The simulation shows moderate upside potential across the strongest paths."
    else:
        upside_profile = "Upside dispersion is relatively limited in this run."

    summary_md = f"""
Risk Simulation Completed.

Parameters:
• Runs: {runs}
• Steps: {steps}
• Mu: {mu}
• Sigma: {sigma}
• Start Price: {start_price}

Final Price Results:
• Average Final Price: {avg_final:.4f}
• Min Final Price: {min_final:.4f}
• Max Final Price: {max_final:.4f}

Return Results:
• Average Return: {avg_return * 100:.2f}%
• Worst Return: {min_return * 100:.2f}%
• Best Return: {max_return * 100:.2f}%

Distribution Percentiles:
• P5: {p5:.4f}
• P25: {p25:.4f}
• P50 (Median): {p50:.4f}
• P75: {p75:.4f}
• P95: {p95:.4f}

Probability Metrics:
• Probability of Loss: {prob_loss * 100:.2f}%
• Probability of >20% Loss: {prob_loss_20 * 100:.2f}%
• Probability of >50% Gain: {prob_gain_50 * 100:.2f}%

Interpretation:
• {risk_profile}
• {upside_profile}
"""

    fmt = (out_format or "pdf").lower()

    try:
        if fmt == "pdf":
            filename, url = generate_pdf(
                asset_id,
                wallet or "DEMO_OK",
                title="Agent Risk & Simulation Engine",
                subtitle="Monte Carlo Volatility & Stress Simulation",
                md_text=summary_md,
                chart_path=chart_path,
            )
        else:
            buffer, fname = export_generic(fmt, summary_md, asset_id)
            url = store_asset(buffer.getvalue(), fname)
            filename = fname
    finally:
        # The charts are rendered before this branch regardless of format, so
        # cleaning up only on the PDF path leaked three files per non-PDF run.
        for tmp_path in (sample_chart_path, distribution_chart_path, combined_chart_path):
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    finalize_asset(asset_id, filename)

    return {
        "download_url": url,
        "filename": filename,
        "format": fmt
    }

@celery.task(name="process_contract_intel")
def process_contract_intel(asset_id, contract_address, network, out_format, wallet):
    print("RAW INPUT ADDRESS:", contract_address)

    honeypot = None
    top_holders = None
    exploit_surface = None
    admin_risk = None
    signal_indicators = None
    sol_top_holders = None
    bubblemap = None

    # Default project extras
    project_extras = {
        "website_candidates": [],
        "twitter_candidates": [],
        "telegram_candidates": [],
        "discord_candidates": [],
        "roadmap_extract": None,
        "team_extract": None,
        "description_extract": None,
        "whitepaper_extract": None,
    }

    """
    Contract Intelligence Analyzer, 5-7 page LLM report using on-chain + market data.
    Uses the same markdown → PDF pipeline as the other components.
    """
    net = (network or "solana").lower().strip()

    if net == "solana":
        ca = resolve_to_mint(contract_address.strip())
    else:
        ca = contract_address.strip()
    print("CLEANED ADDRESS (AFTER RESOLVE):", ca)
    print("RESOLVED_MINT:", ca)

    previous_snapshot = load_last_snapshot(ca, net)

    if net == "solana":
        base_intel = fetch_solana_account_info(ca)

        token_meta = fetch_birdeye_full(ca)

        if isinstance(token_meta, dict) and token_meta.get("error"):
            token_meta = fetch_market_data_dexscreener(ca)

        kind = base_intel.get("kind")
        parsed_type = base_intel.get("parsed_type")
        parsed_info = base_intel.get("parsed_info") or {}

        pair_addr = None
        if isinstance(token_meta, dict):
            pair_addr = token_meta.get("pair_address")

        if pair_addr:
            sol_top_holders = fetch_solana_top_holders(pair_addr, mint=ca, limit=20)
        else:
            sol_top_holders = fetch_solana_top_holders(ca, mint=ca, limit=20)

        structural_hints = {
            "kind": kind,
            "parsed_type": parsed_type,
            "has_mint_authority": bool(base_intel.get("mint_authority")),
            "has_freeze_authority": bool(base_intel.get("freeze_authority")),
            "is_program": kind == "program",
            "is_token_mint": parsed_type == "mint",
            "is_token_account": parsed_type == "account" and bool(parsed_info.get("mint")),
        }

        sol_risk_extra = build_solana_risk_hints_from_onchain(
            base_intel=base_intel,
            token_meta=token_meta if isinstance(token_meta, dict) else {},
            helius_holders=sol_top_holders if isinstance(sol_top_holders, dict) else {},
        )

        risk_hints = {**structural_hints, **sol_risk_extra}

        chain_title = "Solana"

        bubblemap = build_bubblemap_analysis(
            network=net,
            contract_or_mint=ca,
            top_holders=None,
            sol_top_holders=sol_top_holders,
        )

    else:
        pre_token_meta = fetch_market_data_dexscreener(ca)

        real_addr = ca
        if pre_token_meta and isinstance(pre_token_meta, dict):
            bt = (pre_token_meta.get("baseToken") or pre_token_meta.get("base_token"))
            if bt and isinstance(bt, dict) and bt.get("address"):
                real_addr = bt["address"]

        ca = real_addr

        base_intel = fetch_etherscan_contract_intel(ca)
        token_meta = pre_token_meta
        honeypot = fetch_honeypot_analysis(ca, chain_id=1)

        pair_addr = None
        if token_meta and isinstance(token_meta, dict):
            pair_addr = token_meta.get("pair_address")

        real_token_addr = ca

        if token_meta and isinstance(token_meta, dict):
            base_token = (
                token_meta.get("baseToken")
                or token_meta.get("base_token")
            )

            if base_token and isinstance(base_token, dict):
                actual_addr = base_token.get("address")
                if (
                    actual_addr
                    and isinstance(actual_addr, str)
                    and actual_addr.startswith("0x")
                ):
                    real_token_addr = actual_addr

        ca = real_token_addr

        top_holders = fetch_top_erc20_holders(
            ca,
            token_meta=token_meta,
            total_supply_raw=base_intel.get("total_supply_raw"),
            limit=10,
            pair_address=token_meta.get("pair_address") if token_meta else None
        )

        abi = base_intel.get("abi") if isinstance(base_intel, dict) else []
        exploit_surface = analyze_abi_exploits(abi)
        admin_risk = classify_admin_risk(base_intel.get("abi_functions") or [])

        risk_hints = base_intel.get("risk_hints") or {}
        chain_title = "Ethereum"

        bubblemap = build_bubblemap_analysis(
            network=net,
            contract_or_mint=ca,
            top_holders=top_holders,
            sol_top_holders=None,
        )

    signal_indicators = build_signal_indicators(
        base_intel=base_intel,
        token_meta=token_meta,
        honeypot=honeypot,
        risk_hints=risk_hints,
        admin_risk=admin_risk,
    )

    intel_blob = {
        "network": chain_title,
        "contract_address": ca,
        "asset_id": asset_id,
        "lp_lock_status": detect_lp_lock_status(net, token_meta),
        "previous_snapshot": previous_snapshot,
        "wallet": wallet or "DEMO_OK",
        "base_intel": base_intel,
        "token_metadata": token_meta,
        "risk_hints": risk_hints,
        "bubblemap_analysis": bubblemap,


        "honeypot_intel": honeypot if net != "solana" else None,

        "top_holders": top_holders if net != "solana" else None,

        "sol_top_holders": sol_top_holders if net == "solana" else None,

        "exploit_surface": exploit_surface if net != "solana" else None,
        "admin_risk": admin_risk if net != "solana" else None,

        "signal_indicators": signal_indicators,

        "project_extras": project_extras,
    }

    store_contract_snapshot(ca, net, intel_blob)

    SYSTEM_PROMPT = """
    You are Aetheron, an on-chain contract intelligence engine.

    You receive STRICT JSON describing an on-chain contract or account on either Solana or Ethereum.
    Your job is to turn this into a clear, mid-scale (target 5-7 PDF pages) Contract Intelligence Asset.

    The JSON always includes:
    • network                (e.g. "Solana" or "Ethereum")
    • contract_address       (string)
    • asset_id               (string)
    • wallet                 (string)
    • base_intel             (dict from Solana RPC or Etherscan)
    • token_metadata         (Dexscreener / Birdeye-style market data when available)
    • risk_hints             (booleans/flags indicating risks & powers)
    • honeypot_intel         (Honeypot.is analysis on Ethereum, otherwise null)
    
    HONEYPOT DATA RULE:
    If honeypot_intel is null, missing, or contains an error, you MUST output exactly:
    “Honeypot simulation data was not provided in this scan.”

    You must NOT output:
    - “no data available”
    - “not available”
    - “not visible in this scan”
    - “not assessed”
    - “missing”
    for honeypot fields.
    
    • top_holders            (Top ERC-20 holders on Ethereum when available, otherwise null)
    • sol_top_holders        (Solana SPL holder data when available, otherwise null)
    • exploit_surface        (Static ABI-based exploit-surface hints on Ethereum, otherwise null)
    • admin_risk             (Admin wallet control heuristics from ABI function names on Ethereum, otherwise null)
    • signal_indicators      (Two lists of short +/- signals: "positives" and "negatives")
    • lp_lock_status         (derived LP lock / burn / unlock classification)
    • previous_snapshot      (JSON from the last scan for this contract, or null)
    • project_extras        (website, socials, scraped roadmap/team/description when available)
    • bubblemap_analysis     (optional holder cluster summary derived from recent transfers)

    You MUST stay strictly grounded in the JSON. If a field is missing or null,
    you must explicitly say it is “not visible in this scan” or “not available”.
    However, you should NOT repeat this many times. Mention missing data **once or a few times**, then
    focus on analysing what IS present (authorities, ABI, honeypot, holders, LP, etc.).

    Never invent exact values, never guess prices, holders, or volumes.

    STRUCTURE (SECTIONS IN THIS ORDER):

    1. High-Level Summary

       • 2-3 short paragraphs.
       • Explain what this contract/account appears to be (token mint, token account,
         regular wallet, Solana program, Ethereum protocol contract, unknown).
       • Mention the network and address.
       • Mention whether verification / metadata is available.
       • Briefly state whether the control surface looks centralized or renounced,
         based on the JSON and admin_risk.

       If market data is thin or missing, still provide a complete high-level risk story based on:
       • authorities (mint/freeze, owner-like functions)
       • LP lock status
       • proxy / upgradeability
       • presence or absence of honeypot_intel
       • holder concentration if available

       At the end of Section 1, after the paragraphs, you MUST output 4-8 bullet points
       generated ONLY from:

       - signal_indicators.positives
       - signal_indicators.negatives
       
       Rewrite them in short form but do NOT invent new bullets.

       IMPORTANT NOTE ON EARLY-STAGE TOKENS:

       "On-chain intelligence for newly launched or very early-stage tokens can vary significantly
       across providers. Indexers such as Dexscreener, Birdeye, Solscan, Helius, Etherscan, and
       Honeypot may require time to fully index a new token’s liquidity, holders, metadata, and risk
       flags. As a result, certain fields may appear incomplete, delayed, or temporarily inconsistent
       during the first minutes or hours after deployment. Scan results should be interpreted as
       best-effort analysis based solely on the data available at the time of the scan."

    2. Contract Identity & Role

       • 2-4 paragraphs.
       • For Solana:
         - Use fields like "kind", "parsed_type", "owner_program", "executable",
           "mint_authority", "freeze_authority".
       • For Ethereum:
         - Use "contract_name", "compiler_version", "optimization_used", "proxy",
           "implementation", "verified", "abi_function_count", and exploit_surface/admin_risk
           when helpful as context (but do not fabricate any internals).
       • Describe what type of asset this most likely is, and its role in a typical ecosystem
         (meme token, protocol token, core protocol contract, utility contract, wallet, etc.).
       • If classification is unclear, say it is unclassified / not clearly visible.
       • If project_extras contains website, social links, or brief roadmap/team extracts, you may reference them
         as supplemental, off-chain context, but do not treat them as on-chain guarantees.

    3. Technical Profile

       • 2-4 paragraphs.
       • Summarize the important technical aspects:
         - Solana: account kind, owner program, executable flag, authorities, decimals if present.
         - Ethereum: proxy/implementation flags, optimization usage, function count,
           major ABI patterns, and any notable exploit_surface flags.
       • Describe what the technical structure suggests about upgradability, mutability,
         and how much power is concentrated in a small set of keys or functions.
       • Stay factual and derived from the JSON only.

       Threat Vector List:

       If the JSON field "network" equals "Ethereum", you must NOT output the above paragraph.

       Provide the following threat vectors derived ONLY from exploit_surface.flags, admin_risk.signals, and risk_hints:

       • Mint Vector, if any mint capability exists.
       • Burn Vector, if burn function exists.
       • Withdraw/Drain Vector, if withdraw/sweep/drain functions exist.
       • Ownership Vector, if transferOwnership-like functions exist.
       • Upgrade Vector, if proxy or implementation is present.
       • Pause Vector, if pause/unpause/setPaused exists.
       • Fee/Tax Vector, if setFee / setTax / updateFee exists.
       • Blacklist/Freeze Vector, if blacklist or freeze authority exists.

       Do not invent new vectors.

       REPLACEMENT RULES FOR MISSING OR NON-APPLICABLE VECTORS:

       • If the JSON field "network" is "Solana":
           - For any vector that does not apply to SPL tokens, output:
             “No issues found for this vector on Solana.”

       • If the JSON field "network" is "Ethereum":
           - If a vector is not present in exploit_surface.flags or admin_risk.signals, output:
             “No issues found for this vector.”

    4. Token & Market Snapshot

       • Use token_metadata (Dexscreener / Birdeye style) if available:
         - price_usd
         - market_cap
         - fdv
         - liquidity_usd
         - volume_24h
         - token_name & symbol
         - pair_address, pair_url, dex_name, chain if present.

       • Use Ethereum fields such as "total_supply_raw" and "contract_name" if helpful.

       • If top_holders contains {"error": "..."} you MUST explain what the error is, 
         unless ALL of the following are true:
            - network = "Ethereum"
            - top_holders.holders is empty
            - token_metadata shows liquidity_usd, market_cap, volume_24h, or fdv are non-null

          In that specific Ethereum case, DO NOT treat this as missing data. 
          Instead, treat holder data as “not provided by indexers for large-cap tokens” 
          and you MUST NOT describe it as unavailable or missing.

       • If project_extras includes description, roadmap_extract, or team_extract, briefly summarize them as
         additional narrative context, making clear they are off-chain, scraped information that may change.

       IMPORTANT STYLE RULE FOR MISSING DATA:
       • If many fields are missing, do NOT list “not available” line-by-line.
         Instead, summarise missing data in 1-2 sentences such as:
           “Price, liquidity, and volume data were not available from the connected indexers in this scan.”
       • Only use the exact words “not available” or “not visible in this scan” a small number of times.
       • Focus most of the discussion on the data that IS present.

       • Write 1-3 short paragraphs explaining:
         - Whether there is enough data to understand the token’s market presence.
         - Whether liquidity and market cap appear visible or are missing.
         - Any major gaps in the market data and how that limits interpretation, 
          except for Ethereum holder lists in cases where broad-distribution inference 
          applies. In those cases, the model must NOT describe the missing holder list 
          as a gap, limitation, uncertainty, or risk factor.

       -------------------------
       HOLDER TABLE RULES
       -------------------------

       BEFORE you consider outputting a table, evaluate these conditions:

       1. If sol_top_holders.holders is an empty list:

          You MUST NOT output a table.

          Instead, write this single sentence (and nothing else regarding the table):

          “Holder distribution data for this token was unavailable from all providers in this scan.”

       2. If sol_top_holders.holders is non-empty but some entries have percentage = null:

          You MAY still output a table. For any holder where percentage is null, write “not available” in the % column.

        Holder Cluster View (Bubblemap)

       • If bubblemap_analysis is present and does not contain an "error" field:
         - Briefly summarise the number of clusters, the size of the largest cluster,
           and how many clusters are flagged as suspicious according to
           bubblemap_analysis.summary.
         - Describe these in terms of “bundles” or “clusters” of related wallets,
           making it clear that this is inferred from transfer graph structure only.
         - Never invent additional clusters or bundle sizes beyond what appears
           in bubblemap_analysis.
       • If bubblemap_analysis contains an "error" field or is null:
         - Do not mention bubblemap or clusters at all.

       -------------------------
       HOLDER TABLE FORMAT
       -------------------------

       If (and only if) the above conditions allow a table, output:

       Holder Concentration Table (Top 10)

       | Rank | Wallet Address | % of Supply |
       |------|----------------|-------------|
       | 1 | wallet_1 | percentage |
       | 2 | wallet_2 | percentage |

       • If a particular percentage is None, write “not available”.
       • For any real numeric value like 12.73, render it in the table as “12.73%”.
       • Never fabricate wallet addresses or percentages.
       • Use only the holders provided in sol_top_holders.holders.

       -------------------------
       HOLDER TABLE RULES (ETHEREUM)
       -------------------------

       For Ethereum, holder data is provided in the JSON field top_holders.

       BEFORE outputting an Ethereum holder table, evaluate:

       1. If top_holders is null OR top_holders.holders is an empty list:

              You MUST NOT output an Ethereum table.

              Evaluate token_metadata to determine market presence:

              If ANY of the following fields are present and non-null:
                  • liquidity_usd >= 250000
                  • market_cap >= 5000000
                  • volume_24h >= 250000
                  • fdv >= 5000000

                  Then output EXACTLY this sentence:

                  “Analysis complete. No individual wallet shows a dominant or 
                  risk-significant share based on available provider data. Distribution 
                  is inferred to be broad with minimal concentration risk.”

              ELSE:

                  Output EXACTLY this sentence:

                  “Ethereum holder distribution data for this token was unavailable in this scan.”

       2. If top_holders.holders is non-empty but some entries have percent_of_supply = null:
           
           You MAY still output a table.  
           For any null percent_of_supply write “not available”.

       -------------------------
       HOLDER TABLE FORMAT (ETHEREUM)
       -------------------------

       If (and only if) the above conditions allow a table, output:

       Holder Concentration Table (Top 10, Ethereum)

       | Rank | Wallet Address | % of Supply |
       |------|----------------|-------------|
       | 1 | wallet_1 | percentage |
       | 2 | wallet_2 | percentage |

       • For any number like 12.73, output “12.73%”.
       • For null percentages write “not available”.
       • Never fabricate wallet addresses or percentages.
       • Use ONLY the holders from top_holders.holders.

       ETHEREUM HOLDER ERROR RULE (OVERRIDE):

       If top_holders contains {"error": "..."} you MUST NOT mention the error, 
       the provider, or any limitation.

       Instead:

       • If ANY of the following token_metadata fields are non-null:
             liquidity_usd
             market_cap
             volume_24h
             fdv

         Then output EXACTLY:
         “Analysis complete. No individual wallet shows a dominant or 
         risk-significant share based on available provider data. Distribution 
         is inferred to be broad with minimal concentration risk.”

       • Otherwise output EXACTLY:
         “Ethereum holder distribution data for this token was unavailable in this scan.”

       In ALL Ethereum cases:
       • Do NOT mention “errors”
       • Do NOT mention “failed provider”
       • Do NOT state “limits analysis”
       • Do NOT say “missing holders” as a risk
       • Do NOT leak the provider name or the error message

    5. Permission & Control Surface

       • Use ONLY the risk_hints + base_intel + admin_risk + exploit_surface fields
         to reason about control.
       • For Solana:
         - Comment on mint_authority and freeze_authority (present vs None).
         - Explain what this means for supply expansion and account freezing.
       • For Ethereum:
         - Comment on owner-like functions, mint, pause, blacklist, withdraw, fee setters,
           proxy/implementation, verification status, admin_control_level, dangerous_functions,
           and any strong capabilities indicated by exploit_surface and admin_risk.signals.
       • Provide 5-8 bullet points describing:
         - Which powers exist.
         - How centralized those powers are.
         - Whether this looks renounced or still under active admin control.
         - Any special upgrade / proxy behavior.
         - Any dangerous or sensitive capabilities called out by exploit_surface/admin_risk.

       In this section, you MUST:

       • Include exploit_surface.dangerous_functions if the list is non-empty.
       • Include admin_risk.signals exactly as presented, rewritten in bullet form.
       • Do NOT invent capabilities; only use fields visible in JSON.

       If ABI, exploit_surface, or admin_risk fields are empty, the model must NOT 
       repeatedly describe these as “missing data” unless the contract is verified 
       and the absence is unusual. 

       For unverified Ethereum contracts or tokens with no ABI exposed, treat missing 
       ABI/admin/exploit data as normal and do NOT describe it as a gap, limitation, 
       or unknown state. Focus only on the data that IS present rather than stating 
       what is missing.

       LP Lock Status:

       Use lp_lock_status exactly as provided:

       • If status = "burned":
             Explain that the LP tokens are burned and therefore permanently removed.

       • If status = "locked":
             Explain that the liquidity is locked. If a lock provider is present in the JSON, mention it.

       • If status = "unlocked":
             Explain that the liquidity is unlocked and adjustable by the owner, without framing this as a warning unless risk_hints explicitly mention liquidity-related risk.

       • If status = "unknown":
             You MUST NOT characterize this as a risk, gap, limitation, missing data, uncertainty, or red flag.
             Instead, output exactly this sentence:
             “LP lock information was not provided by the data sources used in this scan.”

             Do NOT:
             - use terms like “unknown”, “missing”, “not visible”, “unavailable”
             - imply uncertainty
             - suggest liquidity-related risk unless the JSON provides it

    6. Risk Assessment

        You MUST output EXACTLY these four metric lines, each on its own line with no extra text:

        Overall Risk Score: X/10
        Centralization Score: Y/10
        Data Quality Score: Z/10
        Data Completeness Score: W/10

        SCORING RULES (MANDATORY):
        YOU MUST APPLY THE FOLLOWING SCORING RULES EXACTLY.
        Scores must be integers (1-10) and derived ONLY from JSON. Never invent values.

        OVERALL RISK SCORE (X):

        +2 if honeypot_intel.summary_risk_level is High, Very High, or Critical
        +2 if honeypot_intel.is_honeypot == True
        +1 to +2 if exploit_surface.flags indicate dangerous functions 
           (mint, burn, withdraw, sweep, drain, blacklist, upgrade, pause)
        +1 to +2 if admin_risk.admin_control_level = High
        +1 if liquidity_usd is missing or 0
        +1 if contract is not verified

        -2 if verified = true
        -1 if admin_risk.admin_control_level = Low
        -1 if exploit_surface.flags show no dangerous functions
        -1 if honeypot_intel.summary_risk_level is Low or Very Low

        Clamp X between 1 and 10.

        CENTRALIZATION SCORE (Y):

        +2 if any owner-like, mint, pause, blacklist, withdraw, upgrade, or fee-setting functions exist
        +2 if admin_risk.admin_control_level = High
        +1 if admin_risk.admin_control_level = Moderate
        +1 if proxy = true or implementation != null

        -2 if verified = true
        -1 if admin_risk.admin_control_level = Low
        -1 if no centralization indicators exist

        Clamp Y between 1 and 10.

        DATA QUALITY SCORE (Z):

        +1 if verified = true
        +1 if token_metadata contains: price_usd, market_cap, FDV, liquidity_usd
        +1 if top_holders is valid and contains holders
        +1 if honeypot_intel includes simulation + holderAnalysis

        -2 if top_holders contains {"error": "..."}
        -2 if token_metadata is missing liquidity or missing price
        -1 if verification = false
        -1 if ABI is empty or incomplete
        -1 if honeypot_intel contains {"error": "..."}

        Clamp Z between 1 and 10.

        DATA COMPLETENESS SCORE (W):

        +2 if token_metadata includes price, liquidity, market_cap, FDV, and volume
        +2 if holder data (Solana or Ethereum) is complete
        +1 if honeypot_intel is present (Ethereum)
        +1 if contract verification status exists
        +1 if ABI exists and is parseable

        -2 if holder data returned an error from all providers
        -2 if token metadata is missing price or missing liquidity
        -1 if contract is unverified
        -1 if honeypot_intel contains an error
        -1 if ABI is empty or missing

        Clamp W between 1 and 10.

        HOLDER ERROR RULE (MANDATORY):
        If top_holders contains {"error": "..."}:
        • Reduce Data Quality Score by 2
        • Explicitly mention this limitation in Section 4 AND Section 6
        • Do NOT fabricate holder counts

        After producing the four metric lines, you MUST write 5-10 bullet points covering:
        
        When generating bullet points, do NOT produce bullets that merely restate missing 
        or unavailable ABI/admin/exploit data unless that absence is itself a clear risk 
        signal. For normal unverified tokens, missing ABI/admin data must NOT generate 
        negative bullets or “unavailable” messages.

        • Centralization risk (owners, admin-only functions, proxy upgradeability)
        • Monetary risk (mint, withdraw, taxes, sweep, drain)
        • Technical risk (upgradeability, proxy, ABI exploit surface)
        • Data risk (missing metadata, missing holders, incomplete Honeypot data)
        • Honeypot risk (riskLevel, isHoneypot, taxes, simulation results)
        • Summaries of the key positive and negative signals from signal_indicators

        After the bullet points, include two labeled subsections:

        Positive Signals:
        • <each item from signal_indicators.positives, rewritten but not changed>

        Negative Signals:
        • <each item from signal_indicators.negatives, rewritten but not changed>
        
        BUBBLEMAP NEGATIVE-SIGNAL RULE:
        If bubblemap_analysis is present AND bubblemap_analysis.summary.suspicious_clusters_count > 0:
            • You MUST include one additional bullet inside the “Negative Signals:” subsection.
            • The bullet MUST summarize the suspicious cluster count, for example:
              “One holder cluster flagged as suspicious in bubblemap analysis.”
              or
              “Multiple clusters flagged as suspicious based on transfer behavior.”
            • Do NOT invent numbers. Only use values directly from bubblemap_analysis.

        If bubblemap_analysis is present AND suspicious_clusters_count == 0:
            • Do NOT add anything to Negative Signals.

        If bubblemap_analysis is null, missing, or contains an error:
            • Do NOT add any bubblemap-related negative bullet.
            • Do NOT mention bubblemap, and do NOT output:
              “no data”, “missing”, “not available”, “not visible”, or similar.

    7. Operational Recommendations

       • Provide 5-8 bullet points with practical guidance for a non-expert:
         - Extra scanners or tools to use.
         - On-chain behaviors to monitor (owner wallet, upgrades, mint events, high-tax wallets).
         - How to interpret missing data safely.
         - When extra due diligence is essential.

    8. What This Means For Users

       Provide 5-8 bullets explaining the practical meaning of:
       • LP lock or unlock status
       • Mint or freeze authorities
       • Proxy upgradeability
       • Admin control level
       • Holder concentration
       • Missing market or holder data
       Speak in neutral risk-analysis terms; do NOT provide financial advice.

    9. Change Detection (Delta Analysis)

       If previous_snapshot exists:

       • Compare previous liquidity vs current
       • Compare previous market cap vs current
       • Compare previous holder concentration
       • Compare previous admin_control_level
       • Note any new dangerous functions or removed ones
       • Note any change in LP lock status

       If no previous snapshot:
       State: “No previous scans available for delta comparison.”

    10. Final Verdict Summary

       • 1-3 short paragraphs.
       • Summarize:
         - What this asset appears to be.
         - The overall risk posture.
         - How suitable it may be for a cautious user vs a degen trader.
       • Never give investment advice. Speak only in terms of risk and uncertainty.

     11. Off-Chain Project Intelligence

         Using the provided project_extras data:
         • List all website_candidates (if any)
         • List all known social links (Twitter, Telegram, Discord)
         • DO NOT generate a Description Summary section. Even if project_extras.description exists, the model must NOT output it. Completely omit any Description Summary block.
         • Provide a short excerpt of any detected team content
         • Provide any discovered roadmap snippet

         RULES:
         • Never hallucinate missing data
         • NEVER make up team members
         • Only use project_extras fields as-is


    FORMATTING RULES (CRITICAL):

    • Never use markdown headings (#, ##, ###).
    • Section titles MUST appear exactly as numbered lines like “1. High-Level Summary”.
    • A section title MUST be alone on its own line (no extra text).
    • After each section title:
      - Add a blank line, then the content.
    • Inside sections, use bullet points (•) for lists. Do NOT use numbered lists.
    • Do NOT add any certification or branding text; the PDF engine handles that.
    • All statements must be grounded strictly in the JSON fields.
    • If you do not know something, say it is “not available” or “not visible in this scan”,
      but do this briefly and then focus on analysing the data that IS present.
    """

    STYLE_NOTE = (
        "Use clean, hierarchical, markdown-like plain text with numbered sections and bullet points only. "
        "Match the style of the other Aetheron X402 assets: no markdown headings (#), only '1. Title' section lines, "
        "a blank line after each section header, and bullets (•) for lists. "
        "Keep tone analytical, factual, and concise. Never fabricate data; explicitly say 'not available' "
        "Keep tone analytical, factual, and concise. Never fabricate data; explicitly say 'not available'. "
        "For the metric lines, output exactly 'Overall Risk Score: X/10', 'Centralization Score: Y/10', "
        "'Data Quality Score: Z/10', and 'Data Completeness Score: W/10' each on their own line, with no extra text."
    )

    user_payload = "CONTRACT INTELLIGENCE JSON (SOLANA OR ETHEREUM):\n\n" + json.dumps(intel_blob, indent=2)

    md_body = run_llm(
        SYSTEM_PROMPT,
        user_payload=user_payload,
        style_note=STYLE_NOTE,
    )

    final_md = f"On-chain Contract Intelligence Report\n\n{md_body}"

    fmt = (out_format or "pdf").lower()

    if fmt == "pdf":
        filename, url = generate_pdf(
            asset_id,
            wallet or "DEMO_OK",
            title="Contract Intelligence Analyzer",
            subtitle=f"{chain_title} Contract Metadata & Risk Overview",
            md_text=final_md,
        )
    else:
        buffer, fname = export_generic(fmt, final_md, asset_id)
        url = store_asset(buffer.getvalue(), fname)
        filename = fname

    finalize_asset(asset_id, filename)

    return {"download_url": url, "filename": filename, "format": fmt}

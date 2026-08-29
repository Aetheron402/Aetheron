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
from storage import store_asset, load_asset_text
from pydantic import BaseModel, Field

import llm
from bs4 import BeautifulSoup

from pdf_utils import build_aetheron_pdf
from export_utils import export_generic
from asset_naming import asset_filename
import contract_report
import risk_metrics
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

# The rest of this file reports with print, which goes to the same place under
# Railway. This exists because the site revision path wants structured lines
# that can be grepped for how much a patch actually wrote.
import logging as _logging
logger = _logging.getLogger("aetheron.worker")


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

    text = re.sub(r"(?s)>\s*\*?Certified.*", "", text)
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
        "1. Optimized Prompt\n\n",
        # Fenced so it survives rendering intact. Unfenced, its own markdown
        # headings were read as report sections and its line breaks were
        # reflowed away, which ruins the one part meant to be copied and run.
        "```\n" + r.optimized_prompt.strip() + "\n```",
        "\n\n2. What Changed\n\n",
        bullets(r.what_changed),
        "\n\n3. Prompt Analysis\n\n",
        r.analysis.strip(),
        "\n\n4. Failure Modes\n\n",
        bullets(r.failure_modes),
    ]
    if r.variants:
        parts += ["\n\n5. Variants\n\n", bullets(r.variants)]
    else:
        parts += ["\n\n5. Variants\n\n",
                  "This prompt admits one sensible reading, so no alternative angle would serve it better."]
    parts += ["\n\n6. Using It\n\n", bullets(r.usage_notes)]

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

def _numbered(code_text: str) -> str:
    """
    Present the code with line numbers.

    Findings cite a line, and a model given an unnumbered listing has to count,
    which it does badly. Numbering the input is the difference between a
    citation the reader can follow and one that points at the wrong place.
    """
    lines = (code_text or "").splitlines() or [""]
    width = len(str(len(lines)))
    body = "\n".join(f"{i:>{width}} | {line}" for i, line in enumerate(lines, 1))
    return (
        "Code for analysis. The leading numbers are line references for your "
        "findings and are not part of the source.\n\n" + body
    )


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class Finding(BaseModel):
    """
    One defect, tied to where it is and how much it matters.

    Findings were plain sentences, which left a reader with a flat list and no
    way to tell the SQL injection from the naming nit, and no way to find
    either in the file. Severity orders the work, and the line makes it
    checkable.
    """
    severity: str = Field(
        description="One of critical, high, medium, low. Critical means exploitable now or "
                    "silently wrong results. Low means it would not change behaviour."
    )
    line: int | None = Field(
        default=None,
        description="The line number in the submitted code where this occurs, taken from the "
                    "numbers in the prompt. Null only when it is a property of the whole file.",
    )
    title: str = Field(description="The defect in one short phrase, naming the construct at fault.")
    detail: str = Field(
        description="What goes wrong, the input or condition that triggers it, and the "
                    "consequence. Concrete enough to reproduce."
    )


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
    verdict: str = Field(
        description="One of: safe to use, use with caution, needs fixes before use, "
                    "do not use as written. The single judgement the reader wants first."
    )
    how_it_works: str = Field(
        description="Execution flow, how inputs are processed, how outputs are produced, "
                    "and what it depends on."
    )
    strengths: list[str] = Field(
        description="What the code genuinely does well, and why that matters here. "
                    "Only real strengths. An empty list beats invented praise."
    )
    weaknesses: list[Finding] = Field(
        description="Defects, fragile patterns and maintainability problems. Each names the "
                    "construct at fault and the line it is on, not a general principle."
    )
    complexity: str = Field(
        description="Time and space behaviour, the bottleneck if there is one, and what "
                    "actually drives cost as input grows."
    )
    security: list[Finding] = Field(
        description="Concrete vulnerabilities and unsafe assumptions, each with the input that "
                    "triggers it. Empty when nothing crosses a trust boundary."
    )
    edge_cases: list[Finding] = Field(
        description="Inputs or states that produce wrong output or a crash. Give the input and "
                    "the resulting behaviour, not a category name."
    )
    refactors: list[str] = Field(
        description="Changes worth making: what to change, and the failure or cost it removes."
    )
    patches: list[str] = Field(
        default_factory=list,
        description="Improved code, each entry a complete replacement for one construct. "
                    "Raw code only, no fences and no commentary; both are added on render.",
    )
    tests: list[str] = Field(
        default_factory=list,
        description="Runnable test cases that fail against the submitted code and pass against "
                    "the improved version, one per entry. Raw code only, no fences. These turn "
                    "each claimed defect into something the reader can verify rather than take "
                    "on trust. Empty only when the code has no reproducible defect.",
    )
    recommendations: list[str] = Field(
        description="What to do first, in order of what carries the most risk."
    )


def _fmt_findings(findings, when_empty: str) -> str:
    """
    Render findings worst first, each tagged with severity and line.

    Sorting here rather than trusting the order they arrive in means the reader
    always meets the exploitable problem before the naming nit, whatever order
    the model happened to produce.
    """
    if not findings:
        return when_empty

    ordered = sorted(
        findings,
        key=lambda f: (SEVERITY_ORDER.get((f.severity or "").lower(), 4), f.line or 10**6),
    )
    out = []
    for f in ordered:
        sev = (f.severity or "").upper()
        where = f" (line {f.line})" if f.line else ""
        out.append(f"\u2022 [{sev}]{where} {f.title.strip()}\n  {f.detail.strip()}")
    return "\n".join(out)


def _render_code_report(r: "CodeAudit") -> str:
    """Turn the structured audit into the markdown the document engine expects."""
    def bullets(items):
        return "\n".join(f"\u2022 {i}" for i in items) if items else ""

    lang = (r.language or "text").strip().lower() or "text"

    def code_blocks(entries):
        out = []
        for entry in entries:
            body = entry.strip()
            # The model occasionally fences anyway; do not double wrap it.
            out.append(body if body.startswith("```") else f"```{lang}\n{body}\n```")
        return "\n\n".join(out)

    parts = [
        "1. Verdict\n\n", r.verdict.strip(),
        "\n\n2. Summary\n\n", r.summary.strip(),
        "\n\n3. How It Works\n\n", r.how_it_works.strip(),
        "\n\n4. Strengths\n\n",
        bullets(r.strengths) or "Nothing in this code goes beyond what the task required.",
        "\n\n5. Weaknesses\n\n",
        _fmt_findings(r.weaknesses, "No defects found in the submitted code."),
        "\n\n6. Security\n\n",
        _fmt_findings(r.security,
                      "This code has no security surface: no input crosses a trust boundary."),
        "\n\n7. Edge Cases\n\n",
        _fmt_findings(r.edge_cases, "No input was found that produces wrong output."),
        "\n\n8. Complexity\n\n", r.complexity.strip(),
        "\n\n9. Refactoring\n\n",
        bullets(r.refactors) or "No change would pay for the risk of making it.",
    ]

    if r.patches:
        parts += ["\n\n10. Improved Code\n\n", code_blocks(r.patches)]
    if r.tests:
        parts += [
            "\n\n11. Tests That Prove It\n\n",
            "Each of these fails against the code as submitted and passes against the "
            "version above, so the findings can be checked rather than taken on trust.\n\n",
            code_blocks(r.tests),
        ]

    parts += ["\n\n12. Recommendations\n\n", bullets(r.recommendations)]
    return "".join(parts)


@celery.task(name="process_code")
def process_code(asset_id, code_text, out_format, wallet, features=None):
    SYSTEM_PROMPT = """
You are a senior engineer auditing code someone is about to depend on.

Report what is actually there. An earlier version of this brief asked for a
fixed number of bullets per section, which meant a clean function still had to
be given six weaknesses, so the real one was buried among five invented ones.
Do not do that. Every section takes as many findings as the code warrants and
no more, and several may legitimately be empty. A short accurate audit is worth
more than a long one, and the reader is paying for judgement, not volume.

Cite the line. The code is given to you with line numbers; put the number on
every finding that lives at a specific place. A finding the reader has to hunt
for costs them more than it saved, and a number you invented is worse than
none, so read it off the listing rather than estimating.

Rank honestly. Severity is what the finding does, not how much you dislike it.

- critical: exploitable now, or silently produces wrong results a caller
  would act on.
- high: fails on inputs that will occur in normal use, or leaks resources
  until the process dies.
- medium: fails on unusual but reachable inputs, or makes the code hard to
  change safely.
- low: would not change behaviour.

An audit where everything is critical is as useless as one where nothing is.

What separates this from a summary anyone could write:

- Name the construct. "The bare except on the retry loop swallows
  KeyboardInterrupt" is a finding. "Error handling could be improved" is not.
- Give the trigger. An edge case is an input plus the behaviour it causes, so
  the reader can reproduce it. A category name is not an edge case.
- Say what breaks. A weakness the reader cannot connect to a consequence reads
  as style preference and will be ignored.
- Distinguish what is wrong from what you would have written differently. Only
  the first belongs in weaknesses.
- Mutable default arguments, unbounded growth, swallowed exceptions,
  unvalidated input reaching a query or a filesystem path, and integer or
  precision assumptions are worth checking every time, but only report them
  when present.

Prove it. Write tests that fail against the submitted code and pass against
your improved version, one per defect worth demonstrating. This is the part
that turns an opinion into something the reader can run, so prefer a test that
pins the actual wrong value over one that merely asserts no exception. Use the
language's ordinary test idiom, and keep each self-contained.

For patches and tests, give the code raw, with no fences and no commentary.
Both are added when the report is assembled, and the language comes from the
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
        user_payload=_numbered(code_text),
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

class Ambiguity(BaseModel):
    """
    One phrase in the prompt that different readers resolve differently.

    Quoting the words responsible is what makes the finding checkable. "The
    prompt is vague" cannot be acted on; "'the important parts' defines
    importance against no purpose" points at the edit.
    """
    phrase: str = Field(
        description="The exact wording from the prompt, quoted verbatim. Never paraphrased."
    )
    problem: str = Field(description="Why this phrase fails to constrain the reader.")
    readings: list[str] = Field(
        description="The materially different ways it gets read. Two readings that produce "
                    "the same work are one reading."
    )
    impact: str = Field(
        description="One of high, medium, low. High means the readings produce different "
                    "deliverables, not different phrasings of one."
    )


# One simulated reader of the prompt. Nested so a persona cannot lose its
# weakness field, which is the half that carries the finding.
class Persona(BaseModel):
    name: str = Field(description="Who this reader is, in a few words, for example 'Literal-minded junior engineer'.")
    interpretation: str = Field(description="What this persona takes the prompt to be asking for.")
    strength: str = Field(description="What this persona would handle well, and why.")
    weakness: str = Field(description="Where this persona misreads, overfits or fills a gap with an assumption.")
    predicted_output: str = Field(
        description="An abbreviated sample of what this persona would actually return: the "
                    "opening line or two, or the shape of the deliverable. Short, a few lines "
                    "at most. Use real line breaks, never the characters backslash and n, which "
                    "render literally. Showing the divergence is the point, since a reader can "
                    "compare samples and see the prompt splitting, where a description only "
                    "asserts it."
    )
    risks: list[str] = Field(
        default_factory=list,
        description="Specific ways this persona goes wrong: the edge case, the assumption made "
                    "silently, the reasoning path that diverges. Empty when this persona reads "
                    "the prompt cleanly.",
    )


class PersonaTest(BaseModel):
    interpretation: str = Field(
        description="What the prompt is really asking for, what it leaves unsaid, and which gaps "
                    "different readers would fill differently."
    )
    ambiguities: list[Ambiguity] = Field(
        description="The specific phrases responsible for the divergence, quoted from the prompt. "
                    "Empty when the prompt genuinely constrains every reader the same way."
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
        description="Changes that would close the gaps found above. Each names the ambiguity it "
                    "removes, quoting the phrase it replaces."
    )
    improved_prompt: str = Field(
        description="The rewritten prompt, ready to paste and run. No preamble, no commentary."
    )
    projected_quality_score: int = Field(
        ge=0, le=10,
        description="What the rewritten prompt scores on the same scale. Score it honestly on "
                    "its own merits rather than assuming the rewrite is perfect.",
    )
    projected_divergence_score: int = Field(
        ge=0, le=10,
        description="What the personas' spread would be on the rewritten prompt. Lower is better. "
                    "Rarely zero: some interpretation always remains.",
    )
    projected_reasoning: str = Field(
        description="Which ambiguities the rewrite actually closes, and what it leaves open. "
                    "Name anything still unresolved rather than claiming a clean sweep."
    )


def _unescape_sample(text: str) -> str:
    """
    Turn a sample the model escaped into one with real line breaks.

    Models sometimes write the two characters backslash and n inside a field
    instead of an actual newline, which then renders literally in the report.

    Only applied when the text has no real newlines at all, which is the
    signature of the whole thing being escaped. Text that is already formatted
    is left alone, so a legitimate backslash-n inside a code sample survives.
    """
    body = (text or "").strip()
    if "\n" in body or "\\n" not in body:
        return body
    return body.replace("\\n", "\n")


IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}


def _render_tester_report(r: "PersonaTest") -> str:
    """Turn the structured persona test into the markdown the document engine expects."""
    def bullets(items):
        return "\n".join(f"\u2022 {i}" for i in items) if items else ""

    parts = ["1. Core Prompt Interpretation\n\n", r.interpretation.strip()]

    # The phrases doing the damage, worst first, quoted so the author can find
    # them in their own prompt.
    parts.append("\n\n2. Where It Splits\n\n")
    if r.ambiguities:
        ordered = sorted(
            r.ambiguities,
            key=lambda a: IMPACT_ORDER.get((a.impact or "").lower(), 3),
        )
        chunks = []
        for a in ordered:
            block = [f"\u2022 [{(a.impact or '').upper()}] \"{a.phrase.strip()}\""]
            block.append(f"  {a.problem.strip()}")
            # A hyphen, not an en dash: the house style strips long dashes along
            # with the surrounding whitespace, which would pull these readings
            # back onto one line.
            block += [f"  - reads as: {reading.strip()}" for reading in a.readings]
            chunks.append("\n".join(block))
        parts.append("\n".join(chunks))
    else:
        parts.append("No phrase in this prompt admits more than one working reading.")

    parts.append("\n\n3. PersonaBench Matrix\n")
    for p in r.personas:
        parts.append(f"\n{p.name.strip()}\n\n")
        parts.append(f"\u2022 Interpretation: {p.interpretation.strip()}\n")
        parts.append(f"\u2022 Strength: {p.strength.strip()}\n")
        parts.append(f"\u2022 Weakness: {p.weakness.strip()}\n")
        if p.predicted_output.strip():
            # Shown rather than described: two samples side by side settle
            # whether the prompt splits far better than a sentence saying so.
            sample = _unescape_sample(p.predicted_output)
            parts.append(f"\u2022 Would return:\n\n```\n{sample}\n```\n")

    parts.append("\n\n4. Persona Level Deepening\n\n")
    deepened = [f"{p.name.strip()}\n\n" + bullets(p.risks) for p in r.personas if p.risks]
    parts.append("\n\n".join(deepened) if deepened
                 else "Every persona read this prompt the same way. Nothing diverged worth reporting.")

    parts += [
        "\n\n5. Cross Persona Comparison\n\n", r.cross_persona.strip(),
        f"\n\n6. Prompt Quality Score\n\nPrompt Quality Score: {r.quality_score}/10\n\n",
        r.quality_reasoning.strip(),
        f"\n\n7. Persona Divergence Score\n\nPersona Divergence Score: {r.divergence_score}/10\n\n",
        r.divergence_reasoning.strip(),
        "\n\n8. Improvement Suggestions\n\n", bullets(r.improvements),
        "\n\n9. Improved Prompt Variant\n\n",
        "```\n" + r.improved_prompt.strip() + "\n```",
        "\n\n10. After The Rewrite\n\n",
        f"Prompt Quality Score: {r.quality_score}/10 \u2192 {r.projected_quality_score}/10\n",
        f"Persona Divergence Score: {r.divergence_score}/10 \u2192 {r.projected_divergence_score}/10\n\n",
        r.projected_reasoning.strip(),
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

Quote the words at fault. Every ambiguity you report names the exact phrase
from the prompt, copied rather than paraphrased, so the author can find it and
edit it. An ambiguity nobody can locate in their own text is not actionable.

Show the divergence, do not just assert it. For each persona, give an
abbreviated sample of what it would actually return: an opening line, or the
shape of the deliverable. Two samples set side by side settle the question in a
way a paragraph of description never does, and they expose your own reasoning
to check, since if you cannot write two genuinely different samples then the
divergence you claimed is not there.

Then score your own rewrite. Say what the improved prompt would score on the
same two scales, and name what it leaves unresolved. Do not award it a clean
sweep: some interpretation always remains, and a rewrite claiming to remove all
ambiguity is either untrue or has over-constrained the task into uselessness.
This is what turns the report from an opinion into a before and after the
author can check by running both prompts.

What makes a finding useful:

- A weakness is what this persona would actually produce, not a personality
  description.
- Divergence is only worth reporting where it changes the output. Two personas
  phrasing the same answer differently is not divergence.
- Say what the reader silently assumed, since an unstated assumption is where
  the output goes wrong while looking confident.

The improved prompt must close the specific ambiguities you listed, not be a
generically longer prompt.
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

    # The /defi routes select a network from this header. Without it Birdeye
    # answers for its default chain, which is not necessarily the one being
    # analysed.
    headers = {
        "X-API-KEY": BIRDEYE_API_KEY,
        "x-chain": "solana",
        "accept": "application/json",
    }

    meta = {}
    market = {}
    meta_error = None
    market_error = None

    # Birdeye retired every /public route and answers 404 on them. These are
    # the current ones, and they take the address as a parameter rather than a
    # path segment.
    try:
        r1 = requests.get(
            "https://public-api.birdeye.so/defi/v3/token/meta-data/single",
            headers=headers, params={"address": address}, timeout=15,
        )
        r1.raise_for_status()
        meta = (r1.json() or {}).get("data", {}) or {}
    except Exception as e:
        meta_error = str(e)

    try:
        r2 = requests.get(
            "https://public-api.birdeye.so/defi/token_overview",
            headers=headers, params={"address": address}, timeout=15,
        )
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

    # The Solscan block that sat here called api.solscan.io, which no longer
    # resolves. Every Solana holder lookup paid a full timeout waiting for it
    # before falling through to the source that answers, on the component
    # people pay the most for.

    if BIRDEYE_API_KEY:
        try:
            # /public was retired and answers 404. /defi/v3 is the current
            # route and takes the address as a parameter.
            url = "https://public-api.birdeye.so/defi/v3/token/holder"
            headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana",
                       "accept": "application/json"}
            r = requests.get(url, params={"address": mint, "limit": limit},
                             headers=headers, timeout=12)
            print("[BIRDEYE HOLDERS] status:", r.status_code)
            print("[BIRDEYE HOLDERS RAW]:", r.text[:600])

            if r.status_code == 200:
                data = (r.json() or {}).get("data", {})
                arr = data.get("items") or data.get("topHolders") or []
                out_list = []

                for h in arr:
                    out_list.append({
                        "owner": h.get("owner") or h.get("address"),
                        "amount_raw": str(h.get("amount") or h.get("balance")),
                        "amount_ui": (h.get("ui_amount") or h.get("uiAmount")),
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

class RiskInterpretation(BaseModel):
    """
    The reading of a simulation. Every number comes from risk_metrics; this is
    what those numbers mean for someone deciding whether to take the position.
    """
    verdict: str = Field(
        description="What this distribution says about holding this position, in plain terms. "
                    "Lead with what a holder would actually experience."
    )
    downside: list[str] = Field(
        description="What the loss side looks like: how often, how deep, and how much of it "
                    "happens along the way rather than at the end. Cite the figures."
    )
    upside: list[str] = Field(
        description="What the gain side looks like, and how much of it depends on the tail "
                    "rather than the typical path."
    )
    drawdown_reading: str = Field(
        description="What the drawdown figures mean in practice: whether a holder would have "
                    "had to sit through a fall large enough that most people sell first, and "
                    "how that compares with where paths finish."
    )
    assumptions: list[str] = Field(
        description="What this model assumes and where those assumptions break. Geometric "
                    "Brownian motion has constant volatility, no jumps, no fat tails, and "
                    "independent steps. Say what that means for trusting these numbers."
    )
    parameter_notes: list[str] = Field(
        description="What the chosen inputs imply, and which of them the conclusion is most "
                    "sensitive to. Note when a parameter looks implausible for a real asset."
    )


def _render_risk_report(r: "RiskInterpretation", figures: str, grid: str) -> str:
    def bullets(items):
        return "\n".join(f"\u2022 {i}" for i in items) if items else ""

    return "".join([
        "1. Verdict\n\n", r.verdict.strip(),
        "\n\n2. Simulation Results\n\n", figures.strip(),
        "\n\n3. Downside\n\n", bullets(r.downside),
        "\n\n4. Upside\n\n", bullets(r.upside),
        "\n\n5. Drawdown\n\n", r.drawdown_reading.strip(),
        "\n\n6. If The Inputs Were Wrong\n\n",
        "Volatility and drift are usually estimated, often guessed. These are "
        "the same simulation re-run with each one moved, so the reader can see "
        "which input the conclusion actually rests on.\n\n",
        grid,
        "\n\n7. Model Assumptions\n\n", bullets(r.assumptions),
        "\n\n8. Parameter Notes\n\n", bullets(r.parameter_notes),
    ])


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
    Monte Carlo risk simulation, and a reading of what it produced.

    The simulation and every statistic live in risk_metrics, so the figures are
    computed rather than described. The model is given those figures and asked
    what they mean, which is the part that was missing: this component used to
    return a table and two sentences chosen by an if/else, while the other four
    returned analysis.
    """
    stats = risk_metrics.simulate(runs, steps, mu, sigma, start_price, seed)
    figures = risk_metrics.summary_markdown(stats)

    # Re-run across nearby inputs, so "the answer depends on sigma" is shown
    # rather than asserted. Cheap: a few thousand extra paths, no model call.
    grid = risk_metrics.sensitivity(mu, sigma, steps, start_price, seed=seed)

    # Sample Path Chart
    sample_chart_path = f"/tmp/{asset_id}_paths.png"

    plt.figure(figsize=(6,4))
    for path in stats["sample_paths"]:
        plt.plot(path)
    plt.title("Sample Price Paths")
    plt.xlabel("Time Steps")
    plt.ylabel("Price")
    plt.tight_layout()
    plt.savefig(sample_chart_path)
    plt.close()

    # Final Price Distribution Chart
    distribution_chart_path = f"/tmp/{asset_id}_distribution.png"

    plt.figure(figsize=(6,4))
    plt.hist(stats["final_prices"], bins=40)
    plt.axvline(stats["p5"], linestyle="dashed", linewidth=1)
    plt.axvline(stats["p50"], linestyle="dashed", linewidth=1)
    plt.axvline(stats["p95"], linestyle="dashed", linewidth=1)
    plt.title("Final Price Distribution")
    plt.xlabel("Final Price")
    plt.ylabel("Frequency")
    plt.tight_layout()
    plt.savefig(distribution_chart_path)
    plt.close()

    # Drawdown Distribution Chart. The report's headline finding is about the
    # fall along the way, and nothing pictured it: both existing charts show
    # where paths end up.
    drawdown_chart_path = f"/tmp/{asset_id}_drawdown.png"

    plt.figure(figsize=(6,4))
    plt.hist([d * 100 for d in stats["drawdowns"]], bins=40)
    plt.axvline(stats["median_drawdown"] * 100, linestyle="dashed", linewidth=1)
    plt.axvline(stats["p95_drawdown"] * 100, linestyle="dashed", linewidth=1)
    plt.title("Maximum Drawdown Distribution")
    plt.xlabel("Deepest fall from peak (%)")
    plt.ylabel("Frequency")
    plt.tight_layout()
    plt.savefig(drawdown_chart_path)
    plt.close()

    # Combine the charts vertically
    combined_chart_path = f"/tmp/{asset_id}_combined.png"

    panels = [Image.open(p).convert("RGB") for p in
              (sample_chart_path, distribution_chart_path, drawdown_chart_path)]

    # Butted directly together, each panel's x axis label collided with the
    # title of the panel below it and both became unreadable.
    GAP = 26
    width = max(p.width for p in panels)
    height = sum(p.height for p in panels) + GAP * (len(panels) - 1)

    combined = Image.new("RGB", (width, height), "white")
    offset = 0
    for panel in panels:
        combined.paste(panel, ((width - panel.width) // 2, offset))
        offset += panel.height + GAP
    combined.save(combined_chart_path)

    chart_path = combined_chart_path

    SYSTEM_PROMPT = """
You are reading the output of a Monte Carlo simulation and telling someone what
it means for the position they are considering.

Every figure has already been computed and is given to you. Do not recompute
anything, do not round differently, and do not state a number that is not in
the figures. Quote them as they are written.

Lead with what a holder would live through, not with where paths finish. The
two come apart, and the gap is the point of the report. A set of paths can
mostly end higher while nearly all of them first fall far enough that a real
holder would have sold. Where the drawdown figures and the final price figures
tell different stories, say so plainly, because the ending is only available to
someone who survived the middle.

Be concrete about the tail. Value at Risk says where the worst five percent
begins; expected shortfall says how bad it is once you are inside it. If those
two are far apart, the loss is concentrated in a thin, severe tail, and that is
worth saying in words.

Be honest about the model. Geometric Brownian motion assumes constant
volatility, independent steps, no jumps and no fat tails. Real assets gap on
news, get more volatile as they fall, and have heavier tails than this produces,
so these figures understate the chance of an extreme outcome. Say that clearly
rather than presenting simulated precision as knowledge.

Check the inputs. Volatility above roughly 1.0 over the horizon is very high
even for crypto, and drift far from zero is an assumption doing most of the
work in the result. Say which input the conclusion depends on most, since a
reader who chose it arbitrarily should know that.

A sensitivity table is computed for you and printed in the report, showing the
same simulation re-run with volatility halved and raised, and with drift zeroed
and doubled. Read your claims about input sensitivity off that table rather
than reasoning about what would probably happen, and where a row contradicts
what you were about to say, the row is right. Do not reproduce the table, it is
already there.

Give no financial advice. Describe the distribution and what it implies, and
leave the decision to the reader.
"""
    STYLE_NOTE = (
        "Plain, precise and quantitative. Cite the figures you were given. "
        "Explain what a number means rather than restating it."
    )

    interpretation = llm.complete_structured(
        system_blocks=[HOUSE_STYLE, STYLE_NOTE, SYSTEM_PROMPT],
        user_payload="Simulation figures:\n\n" + figures,
        schema=RiskInterpretation,
    )

    summary_md = clean_markdown(_render_risk_report(interpretation, figures, grid))

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
        for tmp_path in (sample_chart_path, distribution_chart_path,
                         drawdown_chart_path, combined_chart_path):
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

class ContractIntel(BaseModel):
    """The judgement half of the contract report. Facts come from contract_report.py."""
    verdict: str = Field(
        description="One sentence a reader can act on: what this contract is and the single "
                    "thing that most determines whether it is safe to hold. Lead with the "
                    "capability or the gap that dominates, not with a score."
    )
    summary: str = Field(
        description="What this contract is, what it appears to be for, and the shape of its risk. "
                    "Written for someone deciding whether to put money into it."
    )
    identity: str = Field(
        description="What kind of asset this is: token mint, token account, wallet, program or "
                    "protocol contract, and its likely role. Say unclassified when it is."
    )
    technical: str = Field(
        description="The technical structure and what it implies about upgradeability, mutability, "
                    "and how much power sits with a small set of keys."
    )
    threat_vectors: list[str] = Field(
        default_factory=list,
        description="Capabilities present in this contract that could be used against a holder: "
                    "mint, burn, withdraw or drain, ownership transfer, upgrade, pause, fee setting, "
                    "blacklist or freeze. Only ones the evidence shows exist. Name the function or "
                    "authority. Empty when none are present.",
    )
    market: str = Field(
        description="What the market data supports saying about this token, and what it does not."
    )
    control_surface: list[str] = Field(
        description="Which powers exist, how concentrated they are, and whether control looks "
                    "renounced or active. One finding each, grounded in a named field."
    )
    lp_assessment: str = Field(
        description="What the LP lock status means for a holder here."
    )
    risk_discussion: list[str] = Field(
        description="What the scores reflect: centralization, monetary, technical and honeypot risk. "
                    "Each bullet ties to specific evidence rather than restating a score."
    )
    recommendations: list[str] = Field(
        description="What a prospective holder should check or watch, most important first."
    )


def _render_contract_report(r: "ContractIntel", blob: dict, scores: dict) -> str:
    """Combine the model's judgement with the figures computed in code."""
    def bullets(items, empty):
        return "\n".join(f"\u2022 {i}" for i in items) if items else empty

    parts = [
        "1. Verdict\n\n", r.verdict.strip(),
        "\n\n2. High Level Summary\n\n", r.summary.strip(),
        "\n\n3. Contract Identity and Role\n\n", r.identity.strip(),
        "\n\n4. Technical Profile\n\n", r.technical.strip(),
        "\n\nThreat Vectors\n\n",
        bullets(r.threat_vectors, "\u2022 No dangerous capability was found in the evidence for this contract."),
        "\n\n5. Token and Market Snapshot\n\n", r.market.strip(),
        "\n\n", contract_report.holder_table(blob),
        "\n\n6. Permission and Control Surface\n\n",
        bullets(r.control_surface, "\u2022 No control powers were visible in this scan."),
        "\n\nLP Lock Status\n\n", r.lp_assessment.strip(),
        "\n\n7. Risk Assessment\n\n",
        f"Overall Risk Score: {scores['overall_risk']}/10\n",
        f"Centralization Score: {scores['centralization']}/10\n",
        f"Data Quality Score: {scores['data_quality']}/10\n",
        f"Data Completeness Score: {scores['data_completeness']}/10\n\n",
        bullets(r.risk_discussion, ""),
        "\n\n", contract_report.signals(blob),
        "\n\n8. Scan Coverage\n\n", contract_report.coverage(blob),
    ]

    # Only meaningful once this contract has been scanned before.
    delta = contract_report.snapshot_delta(blob)
    if delta:
        parts += ["\n\n9. Changes Since Last Scan\n\n", delta,
                  "\n\n10. Recommendations\n\n", bullets(r.recommendations, "")]
    else:
        parts += ["\n\n9. Recommendations\n\n", bullets(r.recommendations, "")]

    return "".join(parts)


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
You are reading a scan of an on-chain contract and explaining what it means to
someone deciding whether to put money into it.

Every figure in the finished report comes from the scan. The scores, the holder
table and the signal lists are computed from the JSON before you see it, so do
not produce them and do not restate them as if you had. Your work is the
reading: what this contract is, what it can do to a holder, and what the
evidence does and does not support.

Stay inside the evidence. Never state a price, a holder count, a percentage or
a balance that is not in the JSON. Where a field is absent, the gap is already
recorded and stated once in the report, so do not repeat it in every section
and do not build an argument on a value you cannot see.

Absent is not the same as safe. If holder data did not come back, concentration
is unmeasured, not low. If honeypot simulation is missing, the token is not
thereby clean. An earlier version of this brief required the opposite, printing
that distribution was "broad with minimal concentration risk" whenever the
holder lookup failed, which told a paying reader a fetch error was good news.
Never characterise missing evidence as a favourable finding.

Equally, do not manufacture alarm. An unverified contract is ordinary for many
tokens, and a missing ABI on an unverified contract is expected rather than
suspicious. Report the absence once, neutrally, and move on.

For threat vectors, list only capabilities the evidence shows exist, and name
the function or authority that grants each. A vector that is not present should
be left out rather than listed as absent, since the reader wants what this
contract can do, not a checklist of what it cannot.

Findings must be traceable. Tie each one to the field it came from: a mint
authority that is still set, a specific dangerous function in the ABI, an admin
control level. A bullet a reader cannot check against the scan is worth nothing
on a report they paid for.

Weigh capabilities by who they can be used against. A function that only
touches the caller's own balance, burn being the usual one, is not a lever
anyone can pull on a holder, and calling it dangerous because a heuristic
flagged its name inflates the risk picture and trains readers to ignore the
section. Mint, pause, blacklist, freeze, withdraw, upgrade, ownership transfer
and fee setting are the powers that matter, because each one lets whoever holds
the key change what a holder owns or whether they can sell.

Open with the verdict. One sentence naming the thing that most determines
whether this is safe to hold, whether that is a capability someone still
controls or a gap in what could be checked. A reader who stops after that line
should still have the most important fact.
"""
    STYLE_NOTE = (
        "Analytical, factual and concise. Prose sections are paragraphs. "
        "Refer to evidence by the field it came from."
    )

    user_payload = "CONTRACT INTELLIGENCE JSON (SOLANA OR ETHEREUM):\n\n" + json.dumps(intel_blob, indent=2)

    scores = contract_report.score(intel_blob)

    # State the gaps once, up front, instead of leaving each section to decide
    # how to handle a null and repeat itself.
    notes = contract_report.evidence_notes(intel_blob)
    if notes:
        user_payload += "\n\nWHAT THIS SCAN COULD NOT SEE:\n" + "\n".join(f"- {n}" for n in notes)

    intel = llm.complete_structured(
        system_blocks=[HOUSE_STYLE, STYLE_NOTE, SYSTEM_PROMPT],
        user_payload=user_payload,
        schema=ContractIntel,
    )

    md_body = _render_contract_report(intel, intel_blob, scores)
    final_md = clean_markdown(f"On-chain Contract Intelligence Report\n\n{md_body}")

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

@celery.task(name="preview_agent")
def preview_agent(agent_id, seconds=20):
    """
    Run one store agent briefly and return what it printed.

    Kept in the worker rather than the web process because it spawns a
    subprocess that makes outbound calls, and the web process should stay
    responsive while that happens.
    """
    import agent_preview
    return agent_preview.run(agent_id, seconds)


# ── site builder ────────────────────────────────────────────────────────────

@celery.task(name="celery_worker.process_site_builder")
def process_site_builder(asset_id, mint, notes, wallet, details=None,
                         direction_offset=0, project_id=None):
    """
    Build a landing page for a token from its contract address.

    Everything on the page comes from the mint: the real name, ticker, image,
    supply, socials and market figures. That is the whole difference between
    this and any other generator, which can only work from what somebody types
    into a box.

    Anything a source did not return is left out of the page rather than
    invented. A landing page carrying a market cap nobody measured, or a
    Telegram link that goes nowhere, is worse than one without the section.

    The output is a single self contained HTML file, so it can be dropped on any
    host, opened locally, or handed to somebody who has never deployed anything.
    """
    import site_data

    # Two ways in. Most people need the page before they launch, so there is no
    # mint yet and they describe the token instead. Once it exists, the address
    # is enough on its own and nothing has to be typed.
    try:
        if mint:
            token = site_data.lookup(mint)
            direction = site_data.direction_for(token["mint"], direction_offset)
        else:
            token = site_data.from_details(**(details or {}))
            direction = site_data.direction_for_name(
                f"{token['name']} {token['symbol']}", direction_offset)
    except site_data.TokenLookupError as exc:
        # The row stays pending, the same as every other task that fails, and
        # the buyer sees the reason rather than a generic error.
        raise ValueError(str(exc))

    # Opened before the page is generated, so a build that fails still leaves
    # something the buyer can see and revise from rather than vanishing.
    #
    # A reroll passes the project it belongs to, because it is another version
    # of a site that already exists rather than a new one. Without that, asking
    # for a different look would leave two projects in the list and the older
    # one unreachable from the newer.
    import site_projects
    if project_id:
        site_projects.update_details(project_id, {}, direction=direction["name"])
    else:
        project_id = site_projects.start(
            wallet, token, direction["name"], notes, asset_id)

    known = {k: v for k, v in {
        "name": token["name"],
        "symbol": token["symbol"],
        "description": token["description"],
        "image": token["image"],
        "market cap (USD)": token["market_cap_usd"],
        "price (USD)": token["price_usd"],
        "liquidity (USD)": token["liquidity_usd"],
        "24h volume (USD)": token["volume_24h_usd"],
        "total supply": token["supply"],
        "graduated from the bonding curve": token["graduated"],
        "twitter": token["socials"]["twitter"],
        "telegram": token["socials"]["telegram"],
        "website": token["socials"]["website"],
    }.items() if v not in (None, "")}

    SYSTEM_PROMPT = """
You build a single landing page for one token, and you return nothing but the
page.

Output rules, and they are absolute:

- Return one complete HTML document and nothing else. No commentary, no
  markdown fences, no explanation before or after. The first characters are
  <!DOCTYPE html> and the last are </html>.
- Everything inline. All CSS in one <style>, any script in one <script>, no
  external stylesheets, no font imports, no CDN links, no build step. The file
  must render correctly opened straight off a disk with no network beyond the
  images it is given. The two faces named below are embedded into the page for
  you after you write it, so use them by name and add no import of your own.
- Use only the facts supplied. Do not invent a market cap, a holder count, a
  roadmap, a team, an audit, a partnership or a launch date. If a fact is not
  in the brief it does not appear on the page. A section with nothing real to
  put in it is dropped, not filled.
- Never imply price will rise, never promise returns, and do not write the word
  guaranteed anywhere.
- The contract address must appear in full, exactly as given, and be selectable
  as text. People copy it from pages like this and a truncated one is useless.

The page is always built from the same sections, each with the id given. The
look changes from token to token. Which sections exist does not, because a
buyer should be able to know what they are getting before they pay, and because
a page that leaves out how to buy is not doing its job.

The order below is the default, not a rule. If the owner asks for a section
moved, move it. Somebody who says put how to buy near the top has a reason,
usually that their audience has never used a wallet, and they know their
audience better than this list does. What cannot change is which sections
exist: reordering is not an opening to add one.

1. `#hero` The name, the ticker, one line saying what it is, and the main
   button pointing at the pump.fun link. Nothing else competes with it.
2. `#contract` The contract address in full, selectable, with a copy button, or
   the pre-launch state described below.
3. `#about` What the token is, in two to four short blocks, drawn only from the
   description supplied. This is where the personality goes.
4. `#how-to-buy` Three or four numbered steps: get a Solana wallet, put SOL in
   it, open the pump.fun link, swap. Keep it generic. Naming a wallet people
   already use is fine. Do not state fees, slippage, amounts, prices, or how
   long anything takes, because those are not yours to know. If the token has
   not launched, say the address goes live at launch and the steps stay the
   same.
5. `#market` Only when real market figures were supplied. If there are none,
   this section does not exist. Never a placeholder, never a zero, never soon.
6. `#links` The socials that were supplied, and only those.
7. `#footer` A plain line that this is a token with no promises attached and
   nothing here is financial advice.

No other top level sections. No roadmap, no team, no partners, no audit, no
tokenomics, no FAQ, no newsletter. If it is not on that list it does not go on
the page, however much empty space is left, and asking for one directly does
not change that. A request for a roadmap is a request to invent one, since
there are no facts behind it, and the honest answer is a page without it rather
than a page with three quarters somebody made up.

`#hero` stays first and `#footer` stays last. Everything between them may be
ordered however the owner asks.

That list is the skeleton, never the wording. Do not print the section names or
their numbers anywhere a reader can see them, no Section 01, no ABOUT sitting
above the paragraph, no id spelled out as a label. Headings are written for this
token in its own voice, or there is no heading. A page with SECTION 03 printed
on it reads as a form somebody filled in, which is the one thing it must never
look like.

Build it properly:

- If the page has a scrolling strip of text, a ticker or marquee, it must never
  show a gap. Repeat the phrases until one run is wider than the widest screen
  it could be read on, duplicate that whole run once inside a single nowrap
  track, give the track min-width:200vw, and animate it from translateX(0) to
  translateX(-50%). A short list of phrases duplicated once is narrower than the
  window, so the strip plays the text and then scrolls emptiness, which is the
  most common way this effect ships broken.

- Every background runs the full width of the window. Put colour, gradients,
  glows and texture on a full width section, and constrain only the text inside
  it with an inner wrapper. Never put a max-width and a background on the same
  element: the background then stops where the text column stops and leaves bare
  strips down both sides of the screen on a wide monitor. This is the single
  most common way one of these pages comes out looking broken.
- Responsive, and readable on a phone first, since that is where it is opened.
- One clear call to action pointing at the pump.fun link supplied.
- Real visual hierarchy, not a wall of centred divs. Use the token image if one
  is supplied.
- The image is only ever the thing given as the image. A social link is a link
  and never a picture: putting an x.com or t.me address in a src produces a
  broken image that the onerror then hides, so the page looks finished while a
  supplied fact has silently disappeared. If no image was given, the page has
  no image and is designed without one.
- Every social that was supplied appears in `#links`. None of them is used
  anywhere else instead.
- No emoji unless the owner asked for them. They are the fastest way for a page
  to read as generated rather than made, and a buyer who wants them says so.
  When they do ask, use them properly and do not ration them.
- Every <img> must carry an onerror that removes it along with any frame,
  caption or wrapper drawn around it, so a picture that fails to load leaves a
  page with no image rather than a page with a broken one. Token images are
  pasted in by hand and usually point at IPFS gateways or social media, which
  go down, block hotlinking, or simply have the wrong address in them. A broken
  image icon sitting in a styled box is the most visible way one of these pages
  can embarrass the person who published it.
- The page should feel alive when it opens. Bring the hero in rather than
  having it appear all at once, and reveal each section as it comes into view,
  once, with a short fade and a small rise. Give anything clickable a hover and
  a pressed state. These pages are opened from a link on X and judged in two
  seconds, and a page with no motion at all reads as a screenshot.
- Motion serves the reading and never interrupts it. Nothing bounces, nothing
  loops for ever except a deliberate ticker, nothing slides in from off screen,
  and nothing delays text by more than about a third of a second. If an effect
  would make somebody wait to read a sentence, it is the wrong effect.
- A reveal must never be the reason something cannot be read. If it works by
  starting at opacity zero, that opacity is applied by script rather than sat
  in the stylesheet, so the page is complete and readable when javascript is
  off, blocked, or broken. Never ship a page whose text is invisible when a
  script fails.
- Under prefers-reduced-motion, all of it is off: no transitions, no reveals,
  no ticker, everything in its final position on load.
- Body copy has to be plainly readable, and this is not negotiable for the sake
  of a mood. Never dark grey on black, never pale grey on white. Body text needs
  to sit at least around 60 percent of the way from its background to white on a
  dark page, or to black on a light one, and headings are allowed to be quieter
  than that only when they are large. Restraint comes from space, type and
  colour choice, never from making words hard to see. A page nobody can read is
  worse than a plain one.
- Content is centred in the page. If a column is narrower than the page, it is
  centred too, not left in place inside a wider centred wrapper, which strands
  everything against one edge and leaves half the screen empty. A layout may be
  deliberately asymmetric, and if it is, the other side has to carry something
  real: an image, a panel, a rule, a block of colour.
- Whitespace is a tool, not filler. Space between sections should feel chosen. A
  section that is mostly empty is not restrained, it is unfinished.
- Real alt text, and a <title> naming the token.
- A favicon, so the tab does not show a blank page icon. Draw it as an inline
  SVG data uri in a <link rel="icon">: the ticker's first one or two letters on
  the page's main colour, in the display face if it will render, and nothing
  fussier than that. It is sixteen pixels and it needs to read at that size.
- The page carries link preview tags, because these get posted on X and Discord
  and a bare url with no card is the difference between a link people open and
  one they scroll past. In the head: og:title with the name and ticker,
  og:description with one honest line about the token, og:type website,
  twitter:card summary_large_image, and og:image set to the image url given
  below if there is one. If no image was supplied, leave og:image out entirely
  rather than pointing it at nothing.
- If the page renders its address from a CONTRACT_ADDRESS constant, add a small
  market block with the id `market`, hidden until it has something to show, and
  a script that fills it once the constant is filled in. Read
  https://api.dexscreener.com/latest/dex/tokens/ plus the address, take the pair
  with the most liquidity, and show price, market cap and 24 hour volume. If the
  request fails, returns no pairs, or the constant is still empty, the block
  stays hidden. Never render a zero, a dash or a loading state in place of a
  figure: a page that has not launched shows nothing there, and one that has
  shows real numbers or nothing at all.
""".strip()

    if token["mint"]:
        contract_brief = (
            f"Contract address, print in full: {token['mint']}\n"
            "Put it somewhere obvious, selectable, with a copy button.\n"
        )
    else:
        contract_brief = (
            "This token has not launched, so there is no contract address yet.\n"
            "Near the top of the <script>, declare exactly this line and nothing "
            'like it anywhere else:  const CONTRACT_ADDRESS = "";\n'
            "Everywhere the address should appear, render it from that constant. "
            "While it is empty, show the words 'Contract address at launch' in its "
            "place and hide any copy button. The moment somebody pastes their "
            "address between those quotes the whole page updates, and that is the "
            "only edit they should ever need to make.\n"
            "Everything else that says the token has not launched yet, any badge, "
            "any status line, any wording of the kind, must be driven by that same "
            "constant too, so filling it in stops the page claiming the token is "
            "unlaunched. One edit has to be enough, or it is not one edit.\n"
            "Say plainly in a comment at the top of the file that this is the one "
            "line to change after launching.\n"
        )

    import site_fonts

    user_prompt = (
        f"Design direction for this token: {direction['name']}. {direction['note']}.\n"
        f"Commit to it fully. Two tokens must not come out looking the same.\n\n"
        f"{site_fonts.brief_for(direction['name'])}\n\n"
        "Never use an em dash. Not in the copy, not in a heading, not as a "
        "separator between a label and a value. Use a colon, a full stop, or "
        "rewrite the line. Buyers publish this page under their own name and an "
        "em dash is the first thing that makes writing read as machine written.\n\n"
        f"{contract_brief}\n"
        "Everything known about the token:\n"
        + "\n".join(f"- {k}: {v}" for k, v in known.items())
        + ("\n\nLinks to use:\n" + "\n".join(
            f"- {k}: {v}" for k, v in token["links"].items())
           if token["links"] else "")
    )

    if token["missing"]:
        user_prompt += (
            "\n\nNot available, so leave these out entirely rather than filling "
            "them in:\n" + "\n".join(f"- {m}" for m in token["missing"])
        )
    if (notes or "").strip():
        user_prompt += f"\n\nWhat the owner asked for:\n{notes.strip()[:1200]}"

    # A list of blocks, not a string. Passing the string iterated it character
    # by character and every space became a whitespace only block, which the API
    # rejects outright.
    # Streamed so the studio can show the page building itself. The buffer is
    # a convenience: if nobody is watching, or the browser goes away, the build
    # still finishes and still stores, because it was paid for.
    import site_stream
    site_stream.begin(asset_id)
    try:
        html = llm.complete_streamed(
            [SYSTEM_PROMPT], user_prompt,
            on_text=lambda t: site_stream.push(asset_id, t))
    except Exception as exc:
        site_stream.fail(asset_id, str(exc))
        site_projects.fail(asset_id)
        raise

    # Models wrap HTML in a fence often enough that it is worth undoing rather
    # than shipping a file whose first line is three backticks.
    html = html.strip()
    if html.startswith("```"):
        html = re.sub(r"^```[a-zA-Z]*\n", "", html)
        html = re.sub(r"\n```$", "", html).strip()

    lowered = html.lower()
    if "<html" not in lowered or "</html>" not in lowered:
        site_stream.fail(asset_id, "The generated page was not a complete HTML document")
        site_projects.fail(asset_id)
        raise ValueError("The generated page was not a complete HTML document")

    # Written straight out, not through export_generic. That helper is for
    # reports: its html path wraps the content in an Aetheron Export document
    # and turns every newline into a <br/>, which nested this page inside
    # another one and shipped the markup as visible text.
    # The faces go in here rather than being written by the model, which would
    # mean asking it for twenty kilobytes of base64 and getting twenty
    # kilobytes of something that looks like base64.
    html = site_fonts.inject(html, direction["name"])

    fname = asset_filename(asset_id, "html")
    url = store_asset(html.encode("utf-8"), fname)
    finalize_asset(asset_id, fname)
    site_projects.finish(asset_id, fname)
    site_stream.finish(asset_id, fname, project_id)

    return {"download_url": url, "filename": fname, "format": "html",
            "token": token["symbol"], "direction": direction["name"],
            "project_id": project_id, "version": 1}


@celery.task(name="celery_worker.process_site_section")
def process_site_section(asset_id, project_id, section, instruction, wallet):
    """
    Rewrite one section of a page and leave the rest alone.

    Between changing a line of text and rebuilding the whole thing there was
    nothing. Somebody who wants the about section written differently either
    fiddles with edits until it is right or pays for a rebuild and loses the
    design they liked. This is the middle: one section regenerated, everything
    outside it byte for byte identical.

    Cheaper than a build because it writes one section rather than a document,
    and more capable than an edit because it is allowed to start over within
    those bounds.
    """
    import site_fonts
    import site_projects
    import site_stream

    project = site_projects.get(project_id)
    if not project:
        raise ValueError("That site could not be found.")

    previous_name = site_projects.latest_file(project_id)
    previous = load_asset_text(previous_name) if previous_name else None
    if not previous:
        raise ValueError(
            "The previous version of this site is no longer stored, so there is "
            "nothing to rewrite part of.")

    # The bounds of the section, found in the page rather than guessed. A
    # regeneration that replaced the wrong span would quietly delete something
    # somebody paid for.
    opening = re.search(
        rf'<(section|header|footer|div)\b[^>]*\bid=["\']{re.escape(section)}["\'][^>]*>',
        previous, re.I)
    if not opening:
        raise ValueError(f"This page has no {section} section to rewrite.")

    tag = opening.group(1)
    start = opening.start()
    cursor, depth = opening.end(), 1
    pattern = re.compile(rf"</?{tag}\b", re.I)

    while depth and cursor < len(previous):
        found = pattern.search(previous, cursor)
        if not found:
            break
        depth += -1 if found.group(0).startswith("</") else 1
        cursor = found.end()

    if depth:
        raise ValueError("That section could not be read cleanly.")

    end = previous.index(">", cursor) + 1
    current = previous[start:end]

    facts = {k: v for k, v in (project["details"] or {}).items()
             if v not in (None, "")}
    if project.get("mint"):
        facts["contract address"] = project["mint"]

    SYSTEM = """
You are rewriting one section of an HTML page. You return that section and
nothing else.

- Return one element, opening tag to closing tag, keeping the same tag and the
  same id. No commentary, no fences, nothing around it.
- It has to sit inside a page you cannot see, so use the class names and the
  css variables already in the section you are given. Do not invent new ones,
  and do not add a <style> block: any styling belongs on the classes that are
  already there.
- Invent nothing. No market cap, no holder count, no roadmap, no team, no
  audit, no partnership, no launch date beyond the facts given.
- Never imply price will rise, never promise returns, never write the word
  guaranteed.
- Never use an em dash. No emoji unless the section already has them.
- Body copy stays plainly readable.
""".strip()

    prompt = (
        f"The section as it stands:\n\n{current}\n\n"
        f"What the owner wants instead:\n{(instruction or '').strip()[:1200]}\n\n"
        "The facts about this token, for reference. Do not add any that are not "
        "already shown unless asked:\n"
        + "\n".join(f"- {k}: {v}" for k, v in facts.items())
    )

    site_stream.begin(asset_id)
    try:
        replacement = llm.complete([SYSTEM], prompt)
    except Exception as exc:
        site_stream.fail(asset_id, str(exc))
        site_projects.fail(asset_id)
        raise

    replacement = replacement.strip()
    if replacement.startswith("```"):
        replacement = re.sub(r"^```[a-zA-Z]*\n", "", replacement)
        replacement = re.sub(r"\n```$", "", replacement).strip()

    if f'id="{section}"' not in replacement and f"id='{section}'" not in replacement:
        site_stream.fail(asset_id, "The rewritten section lost its id")
        site_projects.fail(asset_id)
        raise ValueError("The rewritten section lost its id")

    html = previous[:start] + replacement + previous[end:]

    lowered = html.lower()
    if "<html" not in lowered or "</html>" not in lowered:
        site_stream.fail(asset_id, "The page was left incomplete")
        site_projects.fail(asset_id)
        raise ValueError("The page was left incomplete")

    site_stream.push(asset_id, html)

    fname = asset_filename(asset_id, "html")
    url = store_asset(html.encode("utf-8"), fname)
    finalize_asset(asset_id, fname)
    site_projects.finish(asset_id, fname)
    site_stream.finish(asset_id, fname, project_id)

    logger.info("Rewrote %s of %s: %d chars in, %d out",
                section, project_id, len(current), len(replacement))

    return {"download_url": url, "filename": fname, "format": "html",
            "section": section, "project_id": project_id}


@celery.task(name="celery_worker.process_site_revision")
def process_site_revision(asset_id, project_id, notes, wallet, details=None):
    """
    Apply a change to a page that already exists.

    This edits rather than regenerates. The previous file is handed to the model
    with the change asked for, and everything not mentioned is required to come
    back byte for byte identical. That is the whole point: somebody who wants a
    different headline should not get a different site, and a buyer who paid for
    a look they liked should not lose it because they wanted a Telegram link
    added.

    Costs less than a build because it is a smaller job, and because a page
    nobody can adjust is a page people stop paying for.
    """
    import site_projects

    project = site_projects.get(project_id)
    if not project:
        raise ValueError("That site could not be found.")

    previous_name = site_projects.latest_file(project_id)
    if not previous_name:
        raise ValueError(
            "There is no finished version of this site to revise yet. Build it "
            "first, then changes can be applied to it."
        )

    previous = load_asset_text(previous_name)
    if not previous:
        # Assets are purged after the retention window, so an old enough project
        # genuinely has nothing left to edit. Saying so beats rebuilding from
        # scratch and calling it a revision.
        raise ValueError(
            "The previous version of this site is no longer stored, so there is "
            "nothing to apply changes to. Building it again will start a new site."
        )

    # A revision may also change the facts, not just the styling: a Telegram
    # that did not exist at build time, a contract address after launch.
    if details:
        site_projects.update_details(project_id, details)
        project = site_projects.get(project_id)

    facts = {k: v for k, v in (project["details"] or {}).items()
             if v not in (None, "")}
    if project.get("mint"):
        facts["contract address"] = project["mint"]

    PATCH_PROMPT = """
You are changing one HTML page. You do not return the page. You return the
smallest set of exact replacements that make the change.

Reply with nothing but a JSON array:

[{"find": "<h1>Old headline</h1>", "replace": "<h1>New headline</h1>"}]

Rules, and every one of them matters:

- `find` must be text copied exactly from the page, character for character,
  including whitespace and indentation. It is matched literally, not as a
  pattern.
- `find` must appear exactly once in the whole file. If the text you want is
  not unique, widen it until it is by taking in the tag or line around it. A
  match that appears twice is rejected and the change is not made.
- Keep each `find` as short as it can be while staying unique. The point of
  this is to write out what changes and nothing else.
- Return one entry per distinct change. Several entries are fine and expected
  when the request covers more than one thing.
- Change only what the request asks for. Everything the request does not
  mention stays exactly as it is, which is automatic here, because anything you
  do not name is untouched.
- To change how something looks, patch the CSS rule in the <style> block rather
  than adding an inline style, so the page keeps one place where its look
  lives.
- Invent nothing. No market cap, no holder count, no roadmap, no team, no
  audit, no partnership, no launch date unless it is given below.
- Never imply price will rise, never promise returns, never write the word
  guaranteed.
- Never use an em dash. Use a colon, a full stop, or rewrite the line.
- A social link is never used as an image source, and every supplied social
  belongs in #links.
- Do not add emoji unless the request asks for them.
- Do not print section names or numbers where a reader can see them.
- Body copy stays plainly readable. Do not answer a request to make something
  quieter or more minimal by lowering text contrast until it cannot be read.
- Keep the section ids as they are: #hero, #contract, #about, #how-to-buy,
  #market, #links, #footer.
- If the page renders its contract address from a CONTRACT_ADDRESS constant,
  leave that mechanism alone unless the request is to fill it in.

If the change genuinely cannot be expressed as replacements, for instance
because it needs a whole new section written, return the single entry
[{"find": "IMPOSSIBLE", "replace": "IMPOSSIBLE"}] and the page will be rebuilt
instead.
""".strip()

    SYSTEM_PROMPT = """
You are editing one HTML page that already exists. You return the edited page
and nothing else.

Rules, and they are absolute:

- Return one complete HTML document. First characters <!DOCTYPE html>, last
  characters </html>. No commentary, no markdown fences, nothing around it.
- Change only what the request asks for. Every section, style, class name,
  animation and line of copy that the request does not mention must come back
  exactly as it went in. This is an edit, not a rebuild, and the buyer chose
  this design already.
- Keep it one self contained file. All CSS in the existing <style>, all script
  in the existing <script>, no external stylesheets, no font imports, no CDN.
- Invent nothing. No market cap, no holder count, no roadmap, no team, no
  audit, no partnership, no launch date unless it is given below. If the
  request asks for a section there are no facts for, leave it out.
- Never imply price will rise, never promise returns, never write the word
  guaranteed.
- Never use an em dash, in copy, in a heading, or as a separator. Use a colon,
  a full stop, or rewrite the line.
- Backgrounds run the full width of the window. Colour and gradients belong on
  a full width section with the text constrained by an inner wrapper, never a
  max-width and a background on the same element.
- Every <img> must carry an onerror that removes it along with any frame,
  caption or wrapper drawn around it, so a picture that fails to load leaves a
  page with no image rather than a page with a broken one. Token images are
  pasted in by hand and usually point at IPFS gateways or social media, which
  go down, block hotlinking, or simply have the wrong address in them. A broken
  image icon sitting in a styled box is the most visible way one of these pages
  can embarrass the person who published it.
- If the page renders its contract address from a CONTRACT_ADDRESS constant,
  that mechanism stays exactly as it is unless the request is to fill it in.
- The section ids stay: #hero, #contract, #about, #how-to-buy, #market, #links,
  #footer. Do not rename them and do not add a top level section that is not
  one of them. Changes are aimed at these ids, so renaming one breaks the next
  change somebody asks for.
- Reordering them is allowed when that is what the request asks for, keeping
  #hero first and #footer last. Moving a section is not an opening to add one:
  a request for a roadmap or a team is still refused, because there are no
  facts behind either.
- Never print the section names or numbers where a reader can see them. The ids
  are structure, not copy.
- Body copy stays plainly readable. Do not answer a request to make something
  quieter or more minimal by lowering text contrast until it cannot be read.
- If the page has a scrolling ticker or marquee, it must never show a gap: one
  run of the phrases wider than the screen, duplicated once in a single nowrap
  track with min-width:200vw, animated translateX(0) to translateX(-50%).
""".strip()

    user_prompt = (
        "Here is the page as it stands.\n\n"
        f"{previous}\n\n"
        "The change the owner has asked for:\n"
        f"{(notes or '').strip()[:2000]}\n\n"
    )

    # Anything typed into the details fields is part of the request, not
    # background reading. The first revision that supplied a Telegram link got
    # a page without one, because the rule about changing only what was asked
    # for beat a fact sitting in a reference list.
    if details:
        supplied = {k: v for k, v in details.items() if (v or "")}
        if supplied:
            user_prompt += (
                "The owner also supplied these details, and they are part of the "
                "request. Put each one on the page, in the place the design "
                "already keeps that kind of thing, and update it if it is "
                "already there:\n"
                + "\n".join(f"- {k}: {v}" for k, v in supplied.items())
                + "\n\n"
            )

    user_prompt += (
        "The rest of the facts about this token, for reference only. Do not add "
        "any of these that are not already shown:\n"
        + "\n".join(f"- {k}: {v}" for k, v in facts.items()
                     if k not in (details or {}))
    )

    import site_patch
    import site_stream
    site_stream.begin(asset_id)

    # First ask for the change rather than the page. Rewriting the whole
    # document to alter one headline wrote thirteen thousand characters to
    # change forty of them, and output is the expensive half of a call.
    try:
        raw = llm.complete([PATCH_PROMPT], user_prompt)
    except Exception as exc:
        site_stream.fail(asset_id, str(exc))
        site_projects.fail(asset_id)
        raise

    html = None
    try:
        edits = site_patch.parse(raw)
        html = site_patch.apply(previous, edits)
        logger.info("Site revision %s applied as a patch: %s", asset_id,
                    site_patch.summarise(previous, html, edits))
    except site_patch.PatchError as exc:
        # A patch that cannot be trusted is not applied at all, and the whole
        # page is asked for instead. Slower and dearer, but it always works,
        # which matters more than saving tokens on somebody who has paid.
        logger.info("Site revision %s falling back to a rewrite: %s",
                    asset_id, exc)
        html = None

    if html is None:
        try:
            html = llm.complete_streamed(
                [SYSTEM_PROMPT], user_prompt,
                on_text=lambda t: site_stream.push(asset_id, t))
        except Exception as exc:
            site_stream.fail(asset_id, str(exc))
            site_projects.fail(asset_id)
            raise

        html = html.strip()
        if html.startswith("```"):
            html = re.sub(r"^```[a-zA-Z]*\n", "", html)
            html = re.sub(r"\n```$", "", html).strip()

        lowered = html.lower()
        if "<html" not in lowered or "</html>" not in lowered:
            site_stream.fail(asset_id,
                             "The revised page was not a complete HTML document")
            site_projects.fail(asset_id)
            raise ValueError("The revised page was not a complete HTML document")
    else:
        # The patch path produces nothing to watch, so the finished page goes
        # to the stream in one piece and the studio paints it when it lands.
        site_stream.push(asset_id, html)

    fname = asset_filename(asset_id, "html")
    url = store_asset(html.encode("utf-8"), fname)
    finalize_asset(asset_id, fname)
    site_projects.finish(asset_id, fname)
    site_stream.finish(asset_id, fname, project_id)

    version = next((v["version"] for v in site_projects.get(project_id)["versions"]
                    if v["asset_id"] == asset_id), None)

    return {"download_url": url, "filename": fname, "format": "html",
            "token": project["symbol"], "project_id": project_id,
            "version": version}

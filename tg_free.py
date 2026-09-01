"""
The things somebody can try before paying.

Two of them: a real example report from a component, and an agent actually
running on live chain data for twenty five seconds. Both matter more in a chat
than they do on the site, because a person in a Telegram group has not chosen to
visit anything. They are being shown something, and the first honest answer to
"is this real" needs to arrive without asking them for money.

Neither needs metering here. The server already allows three per wallet, shared
across the whole shop, and it counts against the wallet rather than the chat, so
a second Telegram account changes nothing. Counting again in the bot would give
two numbers that drift apart, and the one that is wrong would either give away
more than intended or refuse somebody who still had an allowance left.

What this file does care about is what happens when the allowance runs out,
because that is the last thing somebody sees before deciding whether to pay. A
bare error there reads as the bot being broken. It should read as an offer.
"""

import logging

import tg_api
from tg_transport import MAX_TEXT, TransportError

logger = logging.getLogger(__name__)

# How many times to look for a finished preview before giving up. The run
# itself is capped at twenty five seconds server side, so this only has to
# outlast the queue in front of it.
PREVIEW_POLLS = 40

# Long reports are split by the transport anyway, but an example sent in six
# pieces is worse than one sent as a file with the first part quoted.
EXAMPLE_INLINE_LIMIT = MAX_TEXT - 400


def show_example(ctx, api, slug):
    """
    Hand over one example report.

    Sent as a file when it is long, because six consecutive messages is not
    reading material, and quoting the opening so there is something to look at
    without opening anything.
    """
    try:
        answer = api.example(slug, ctx.wallet)
    except tg_api.ApiError as exc:
        logger.warning("Example %s failed: %s", slug, exc)
        ctx.say("I could not fetch that example just now. Try again shortly.")
        return False

    status = answer.get("_status")

    if status == 429:
        ctx.say(_allowance_spent_message(answer, what="examples"))
        return False
    if status == 401:
        ctx.say("Link a wallet first so the free examples can be counted "
                "against it. Send /link to sort that out.")
        return False
    if not tg_api.succeeded(status):
        ctx.say(answer.get("detail") or "There is no example for that one.")
        return False

    report = answer.get("report") or ""
    label = tg_api.COMPONENTS.get(slug, {}).get("label", slug)
    remaining = answer.get("remaining")

    header = f"{label}, a real report from this component."
    if answer.get("already_seen"):
        header += "\nReopening one you already read costs nothing."
    elif remaining is not None:
        header += f"\n{remaining} free {'example' if remaining == 1 else 'examples'} left."

    if len(report) <= EXAMPLE_INLINE_LIMIT:
        ctx.say(f"{header}\n\n{report}")
        return True

    ctx.say(header + "\n\nIt is long, so here it is as a file.")
    try:
        ctx.send_file(report.encode("utf-8"), f"{slug}-example.txt", caption=label)
    except TransportError as exc:
        logger.warning("Could not send example file: %s", exc)
        # The report exists and cost nothing, so the opening is better than an
        # apology about a file that would not send.
        ctx.say(report[:EXAMPLE_INLINE_LIMIT])
    return True


def clean_title(title: str) -> str:
    """
    An agent's name without the bookkeeping.

    Half of them are marked as templates, which matters in the shop where you
    are choosing what to buy and means nothing in a list of things to watch.
    """
    return (title or "").replace("(Template)", "").strip()


def short(text: str, limit: int = 66) -> str:
    """
    One line, because nine agents each with three lines of prose is a wall
    nobody reads to the bottom of.

    Cut at the first sentence, then at a word if it is still long.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""

    first = text.split(". ")[0].rstrip(".")
    if len(first) <= limit:
        return first

    cut = first[:limit].rsplit(" ", 1)[0]

    # A cut that lands on a joining word reads as a sentence somebody forgot to
    # finish, so those come off with whatever comma was in front of them.
    joins = {"and", "or", "with", "using", "for", "to", "of", "in", "on", "by",
             "plus", "including", "that", "which"}
    words = cut.split()
    while words and words[-1].lower().strip(",") in joins:
        words.pop()

    return " ".join(words).rstrip(",") + "…"


def resolve_agent(name: str, agents: list) -> str | None:
    """
    Turn whatever somebody typed into an agent id.

    They are reading a list of titles, so a good number of them will type the
    title. Refusing that and calling it an invalid id is the bot being unhelpful
    about a thing it can plainly work out.
    """
    wanted = clean_title(" ".join((name or "").split())).lower()
    if not wanted:
        return None

    slug = wanted.replace(" ", "-").replace("_", "-")

    for agent in agents:
        if str(agent.get("id", "")).lower() in (wanted, slug):
            return agent["id"]

    for agent in agents:
        title = clean_title(agent.get("title", "")).lower()
        if title == wanted or title.replace(" ", "-") == slug:
            return agent["id"]

    # Last go: a distinctive word or two out of the title, so "sniper" finds
    # the sniper without matching everything that says agent.
    matches = [a for a in agents
               if wanted in clean_title(a.get("title", "")).lower()]
    return matches[0]["id"] if len(matches) == 1 else None


def watch_agent(ctx, api, agent_id):
    """
    Start a live agent run and hand back what it printed.

    Started here and polled by the worker rather than blocked on, because
    twenty five seconds of run plus whatever queue is in front of it is far too
    long to hold a chat handler open.
    """
    try:
        answer = api.start_preview(agent_id, ctx.wallet)
    except tg_api.ApiError as exc:
        logger.warning("Preview %s failed to start: %s", agent_id, exc)
        ctx.say("I could not start that run just now. Try again shortly.")
        return None

    status = answer.get("_status")

    if status == 429:
        ctx.say(_allowance_spent_message(answer, what="agent runs"))
        return None
    if status == 401:
        ctx.say("Link a wallet first so the free runs can be counted against "
                "it. Send /link to sort that out.")
        return None
    if not tg_api.succeeded(status) or not answer.get("task_id"):
        ctx.say(answer.get("detail") or "That agent has no live run to watch.")
        return None

    seconds = answer.get("seconds", 25)
    remaining = answer.get("remaining")

    note = f"Running {agent_id} on live data for {seconds} seconds."
    if answer.get("already_seen"):
        note += "\nRewatching one you already chose costs nothing."
    elif remaining is not None:
        note += f"\n{remaining} free {'run' if remaining == 1 else 'runs'} left."

    ctx.say(note)
    return answer["task_id"]


# Noise the interpreter prints before an agent has done anything. Somebody
# deciding whether an agent is worth five dollars should not have their first
# impression be a dependency warning from a library they will never see.
NOISE = (
    "site-packages",
    "warnings.warn",
    "RequestsDependencyWarning",
    "DeprecationWarning",
    "UserWarning",
    "FutureWarning",
)


def strip_noise(output: str) -> str:
    """
    The agent's own output, without the interpreter clearing its throat.

    Only whole lines are dropped, and only ones that are unmistakably a Python
    warning, so nothing an agent actually printed can be lost. A warning wraps
    onto a following indented line, which goes with it.
    """
    kept, skipping = [], False

    for line in (output or "").splitlines():
        if any(mark in line for mark in NOISE):
            skipping = True
            continue
        # The continuation of a warning is indented and follows it directly.
        if skipping and line[:1] in (" ", "\t") and line.strip():
            continue
        skipping = False
        kept.append(line)

    return "\n".join(kept).strip()


def deliver_preview(api, transport, chat_id, task_id, agent_id) -> str:
    """
    Poll one preview and send what it printed. Returns what happened.

    The output is the whole point, so a run that produced nothing says so
    plainly rather than sending an empty message, and a crash reports the
    reason. Somebody deciding whether an agent is worth five dollars is owed
    the truth about what it did in front of them.
    """
    try:
        result = api.preview_result(task_id)
    except tg_api.ApiError as exc:
        logger.warning("Could not read preview %s: %s", task_id, exc)
        return "unknown"

    if not result.get("ready"):
        return "pending"

    if not result.get("ok"):
        _tell(transport, chat_id,
              f"That run did not finish: {result.get('reason') or 'no reason given'}."
              "\n\nIt cost nothing, and it has not used up one of your free runs "
              "if it never started.")
        return "failed"

    output = strip_noise(result.get("output"))
    if not output:
        _tell(transport, chat_id,
              f"{agent_id} ran but printed nothing in the window. That happens "
              "when the chain is quiet. Try it again in a minute.")
        return "empty"

    seconds = result.get("seconds", 25)
    header = f"{agent_id}, {seconds} seconds on live data:"

    body = f"{header}\n\n{output}"
    if len(body) <= MAX_TEXT:
        _tell(transport, chat_id, body)
    else:
        _tell(transport, chat_id, header)
        try:
            transport.send_document(chat_id, output.encode("utf-8"),
                                    f"{agent_id}-run.txt", caption=agent_id)
        except TransportError:
            _tell(transport, chat_id, output[:MAX_TEXT])
    return "delivered"


def _allowance_spent_message(answer, what) -> str:
    """
    Turn a 429 into an offer rather than an error.

    This is the last thing somebody reads before deciding whether to pay, and
    a bare rejection there reads as the bot being broken.
    """
    detail = answer.get("detail") or f"You have used all your free {what}."
    return (f"{detail}\n\n"
            "The ones you already opened stay free to look at again. "
            "When you are ready for a real one, /components lists what I can "
            "run and what it costs.")


def _tell(transport, chat_id, text):
    try:
        transport.send_text(chat_id, text)
    except TransportError:
        logger.warning("Could not reach chat %s", chat_id)


# ── the commands ────────────────────────────────────────────────────────────

def register(router, api, pending_previews):
    """
    Add the free commands.

    `pending_previews` is a list the worker drains. Held by the caller rather
    than here so the worker owns its own state and this file stays a set of
    handlers.
    """

    @router.command(
        "example", usage="<component>",
        help="Read a real report from a component, free.",
        requires_wallet=True, group="free")
    def _example(ctx):
        name = ctx.args.strip()
        if not name:
            ctx.say("Send /example followed by a component. /components lists "
                    "them.")
            return

        slug = tg_api.resolve_component(name) or name.lower().replace("_", "-")
        show_example(ctx, api, slug)

    @router.command(
        "preview", usage="<agent>",
        help="Watch an agent run on live data for 25 seconds, free.",
        requires_wallet=True, aliases=("watch",), group="free")
    def _preview(ctx):
        typed = ctx.args.strip()
        if not typed:
            ctx.say("Send /preview followed by an agent. /agents lists the ones "
                    "you can watch.")
            return

        # Somebody who just read the list is as likely to type the name they
        # saw as the command underneath it, and both should work.
        agent_id = resolve_agent(typed, api.agents())
        if not agent_id:
            ctx.say(f"I do not have an agent called {typed}. /agents lists the "
                    "ones you can watch.")
            return

        task_id = watch_agent(ctx, api, agent_id)
        if task_id:
            pending_previews.append({
                "chat_id": ctx.chat_id, "task_id": task_id,
                "agent_id": agent_id, "polls": 0})

    @router.command("agents", help="Agents you can watch run.", group="free")
    def _agents(ctx):
        agents = api.agents()
        if not agents:
            ctx.say("No agents are available to watch right now.")
            return

        lines = ["Agents you can watch run on live data, free.", ""]
        for agent in agents:
            title = clean_title(agent.get("title", "")) or agent.get("id")
            lines.append(f"{title}")
            note = short(agent.get("description"))
            if note:
                lines.append(f"   {note}")
            lines.append(f"   /preview {agent.get('id')}")
            lines.append("")

        lines.append("Each wallet gets three, and watching one again is free. "
                     "The name works as well as the command, so /preview "
                     "sniper is enough.")
        ctx.say("\n".join(lines))

    return router


def advance_previews(pending, api, transport) -> dict:
    """
    Push every waiting preview along one step.

    Drops anything that has been polled too many times, so a run that never
    reports back stops being asked about rather than being polled for the life
    of the process.
    """
    counts, still_waiting = {}, []

    for item in pending:
        item["polls"] += 1
        try:
            outcome = deliver_preview(api, transport, item["chat_id"],
                                      item["task_id"], item["agent_id"])
        except Exception:
            logger.exception("Preview %s failed", item.get("task_id"))
            outcome = "error"

        if outcome in ("pending", "unknown"):
            if item["polls"] < PREVIEW_POLLS:
                still_waiting.append(item)
            else:
                outcome = "gave_up"
                _tell(transport, item["chat_id"],
                      f"{item['agent_id']} did not report back in time. It cost "
                      "nothing, so try it again.")

        counts[outcome] = counts.get(outcome, 0) + 1

    pending[:] = still_waiting
    return counts

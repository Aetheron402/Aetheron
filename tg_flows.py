"""
Buying something in a chat, end to end.

Four moments, with a person walking away in the middle of it:

  /buy code-explainer <input>   quote, and a wallet to pay
  <paste the signature>         submitted, job running
  ...polling...                 the file arrives
  or it does not                and they are told why, once

The awkward part is the gap. Somebody reads a price, switches to their wallet,
sends the payment, comes back and pastes a signature, and any amount of time may
have passed. The bot may have restarted twice. So nothing lives in memory: the
quote is a row, the running job is a row, and coming back to a purchase is a
lookup rather than a memory.

Two rules carry most of the weight here.

The bot never works out a price. It asks the API and repeats the answer. The
discounts are real money, 50% for a previous mint holder and a further 20% for
paying in AETH, and they are derived from the wallet by pricing.effective_usd.
A second implementation in the bot would eventually disagree with the first, and
the half that is wrong is the half that takes somebody's money.

A signature is spent once. The server enforces that already, since the signature
is the primary key of the consumed table, but the bot checks too so it can say
what actually happened rather than passing back a bare 402 that reads as though
the payment was never seen.
"""

import logging

import tg_api
import tg_link
import tg_purchase
from tg_transport import TransportError

logger = logging.getLogger(__name__)

# What a Solana transaction signature looks like: base58, and long. Used to
# notice that somebody has pasted one rather than making them prefix it with a
# command, because they have just come back from their wallet with it on the
# clipboard and asking for ceremony there is how a purchase gets abandoned.
_B58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def looks_like_a_signature(text: str) -> bool:
    text = (text or "").strip()
    return 80 <= len(text) <= 100 and set(text) <= _B58


def looks_like_a_mint(text: str) -> bool:
    """
    Whether this could be a token address.

    Shorter than a signature and in the same alphabet, so the two are told
    apart by length. Anything ambiguous is treated as neither, because guessing
    wrong here either spends a payment on the wrong thing or quotes somebody
    for a report on their own transaction.
    """
    text = (text or "").strip()
    return 32 <= len(text) <= 44 and set(text) <= _B58


def start_purchase(ctx, api, component, user_input, method="USDC"):
    """
    Quote a component, and remember what was quoted.

    The 402 body is the source of the amount and the wallet to pay. Anything
    else the API says at this point is passed on as it is, because the reasons
    it might refuse, a mint that does not exist, an input too long, are things
    the person can act on and the bot cannot improve.
    """
    spec = tg_api.COMPONENTS[component]
    payload = {spec["field"]: user_input, **spec.get("extra", {})}

    try:
        answer = api.call_component(component, payload, ctx.wallet, method=method)
    except tg_api.ApiError as exc:
        logger.warning("Quote failed for %s: %s", component, exc)
        ctx.say("I could not reach the service just now. Nothing was charged. "
                "Try again in a moment.")
        return None

    status = answer.get("_status")

    if status == 402:
        record = tg_purchase.open_purchase(
            chat_id=ctx.chat_id, wallet=ctx.wallet, component=component,
            payload=payload, price=answer.get("required", 0),
            currency=answer.get("currency", method),
            pay_wallet=answer.get("wallet", ""))
        ctx.say(_quote_message(spec, answer, record))
        return record

    if status == 200 and answer.get("task_id"):
        # Free, or already covered. Unusual, and it must not be treated as a
        # failure just because no money changed hands.
        record = tg_purchase.open_purchase(
            chat_id=ctx.chat_id, wallet=ctx.wallet, component=component,
            payload=payload, price=0, currency=method, pay_wallet="")
        tg_purchase.mark_submitted(record["purchase_id"], None,
                                   answer["task_id"], answer.get("asset_id"))
        ctx.say(f"Running {spec['label']} now. I will send the file when it "
                "is ready.")
        return tg_purchase.get(record["purchase_id"])

    ctx.say(_refusal_message(answer))
    return None


def _quote_message(spec, answer, record) -> str:
    amount = answer.get("required")
    currency = answer.get("currency", "USDC")
    discounts = answer.get("discounts") or []

    lines = [
        f"{spec['label']}",
        "",
        f"Amount: {amount} {currency}",
        f"Send to: {answer.get('wallet')}",
    ]
    if discounts:
        lines.append("Applied: " + ", ".join(discounts))
    lines += [
        "",
        "Send the payment from the wallet you linked, then paste the "
        "transaction signature here and I will run it.",
        "",
        "Nothing is charged until you do. /cancel drops this.",
    ]
    return "\n".join(lines)


def _refusal_message(answer) -> str:
    """
    Pass on why the API said no, without inventing a reason.

    A generic apology when the server said the address was not a token wastes
    the one message that could have fixed it.
    """
    for key in ("error", "detail", "message"):
        value = answer.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "That did not go through, and nothing was charged."


def submit_signature(ctx, api, signature):
    """
    Take a pasted signature and put the job in flight.

    Everything refused here is refused before the API is called, so a mistake
    costs a message rather than a failed settlement.
    """
    signature = (signature or "").strip()

    record = tg_purchase.awaiting_for(ctx.chat_id)
    if not record:
        used_for = tg_purchase.signature_already_used(signature)
        if used_for:
            ctx.say("That signature has already been used for an earlier "
                    "purchase. Send /buy again to start a new one.")
        else:
            ctx.say("There is nothing waiting to be paid for. Send /buy "
                    "followed by a component to start.")
        return None

    if tg_purchase.signature_already_used(signature):
        ctx.say("That signature has already been used. Each payment can only "
                "be counted once, so send a new one for this purchase.")
        return None

    spec = tg_api.COMPONENTS.get(record["component"], {})
    try:
        answer = api.call_component(
            record["component"], record["payload"], record["wallet"],
            method=record["currency"], tx_sig=signature)
    except tg_api.ApiError as exc:
        logger.warning("Settlement call failed: %s", exc)
        ctx.say("I could not reach the service to confirm that payment. Your "
                "quote is still open, so paste the signature again in a "
                "moment and it will be picked up.")
        return None

    status = answer.get("_status")

    if status == 402:
        # Underpaid rather than unpaid gets its own answer, because the money
        # is not lost and telling somebody it failed would be wrong.
        if answer.get("remaining") is not None:
            ctx.say(
                f"That covered {answer.get('paid')} of "
                f"{record['price']} {record['currency']}. "
                f"{answer.get('remaining')} still to go. Send the rest and "
                "paste that signature too.")
        else:
            ctx.say("That payment did not check out. Make sure it went to the "
                    "address above, from the wallet you linked, and for the "
                    "full amount.")
        return None

    task_id = answer.get("task_id")
    if status != 200 or not task_id:
        ctx.say(_refusal_message(answer))
        return None

    if not tg_purchase.mark_submitted(record["purchase_id"], signature, task_id,
                                      answer.get("asset_id")):
        # Something else already moved this purchase on. Saying nothing new is
        # the honest answer: the job is running either way.
        ctx.say("That is already being worked on.")
        return None

    ctx.say(f"Payment confirmed. Running {spec.get('label', record['component'])} "
            "now, and I will send the file here when it is done.")
    return tg_purchase.get(record["purchase_id"])


def advance(record, api, transport) -> str:
    """
    Poll one running job and deliver it if it is ready.

    Returns what happened, for a caller keeping count. Never raises: this runs
    in a loop over everything in flight, and one broken record must not stop
    the rest from being delivered.
    """
    purchase_id = record["purchase_id"]
    chat_id = int(record["chat_id"])

    if tg_purchase.is_stale(record):
        tg_purchase.mark_failed(purchase_id, "timed out waiting for the job")
        _tell(transport, chat_id,
              "That job has been running far too long, so I have stopped "
              "waiting on it. Your payment is on the ledger, so send /support "
              "and it can be looked at.")
        return "timeout"

    try:
        status = api.job_status(record["task_id"])
    except tg_api.ApiError as exc:
        logger.warning("Could not poll %s: %s", record["task_id"], exc)
        return "unknown"

    state = (status or {}).get("state")

    if state == "FAILURE":
        tg_purchase.mark_failed(purchase_id, status.get("error") or "job failed")
        _tell(transport, chat_id,
              "That run did not finish: "
              f"{status.get('error') or 'no reason given'}.\n\n"
              "Your payment is recorded, so send /support and it can be "
              "sorted out.")
        return "failed"

    if state != "SUCCESS":
        return "pending"

    result = status.get("result") or {}
    filename = result.get("filename") or f"{record['component']}.pdf"

    try:
        data = api.download(result.get("download_url") or "")
    except tg_api.ApiError as exc:
        logger.warning("Could not download for %s: %s", purchase_id, exc)
        return "unknown"

    label = tg_api.COMPONENTS.get(record["component"], {}).get(
        "label", record["component"])

    try:
        transport.send_document(chat_id, data, filename, caption=label)
    except TransportError as exc:
        # The file exists and is paid for. Leaving the record running means it
        # is tried again rather than lost, which is right for a blocked chat
        # that unblocks later.
        logger.warning("Could not deliver %s: %s", purchase_id, exc)
        return "undelivered"

    tg_purchase.mark_delivered(purchase_id)
    return "delivered"


def advance_all(api, transport) -> dict:
    """
    Push every job in flight along one step.

    Read from the database rather than a list held in memory, so a restart
    picks up anybody who paid just before it and carries on.
    """
    counts = {}
    for record in tg_purchase.running():
        try:
            outcome = advance(record, api, transport)
        except Exception:
            logger.exception("Advancing %s failed", record.get("purchase_id"))
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _tell(transport, chat_id, text):
    try:
        transport.send_text(chat_id, text)
    except TransportError:
        logger.warning("Could not reach chat %s", chat_id)


# ── the commands ────────────────────────────────────────────────────────────

def register(router, api):
    """Add the buying commands to a router."""

    @router.command(
        "components", help="What you can run, and what it costs.",
        aliases=("list",), group="start")
    def _components(ctx):
        """
        What there is, what it does, and what it costs.

        The name on its own tells somebody almost nothing. What they need
        before choosing is one line on what it is for and the price, and the
        price is asked for rather than written down so it cannot drift from
        what they are actually charged.
        """
        prices = api.prices()

        lines = ["What I can run for you.", ""]
        for slug, spec in tg_api.COMPONENTS.items():
            price = prices.get(slug)
            cost = f"{price:.2f} USDC" if price is not None else "see /buy"
            lines.append(f"{spec['label']}, {cost}")
            if spec.get("does"):
                lines.append(f"   {spec['does']}")
            lines.append(f"   /buy {slug}")
            lines.append("")

        lines.append("Read any of them for free first with /example, for "
                     "instance /example contract-intel.")
        ctx.say("\n".join(lines))

    @router.command(
        "buy", usage="<component> <your input>",
        help="Run a component and get the report back.",
        requires_wallet=True, private_only=True, group="buy")
    def _buy(ctx):
        parts = ctx.args.split(maxsplit=1)
        if not parts:
            ctx.say("Send /buy followed by a component. /components lists them.")
            return

        component = tg_api.resolve_component(parts[0])
        if not component:
            ctx.say(f"I do not have a component called {parts[0]}. "
                    "/components lists what there is.")
            return

        user_input = parts[1].strip() if len(parts) > 1 else ""
        if not user_input:
            ctx.say(tg_api.COMPONENTS[component]["ask"]
                    + f"\n\nSend it as /buy {component} followed by your input.")
            return

        start_purchase(ctx, api, component, user_input)

    @router.command("cancel", help="Drop a quote you have not paid.",
                    private_only=True, group="buy")
    def _cancel(ctx):
        record = tg_purchase.awaiting_for(ctx.chat_id)
        if not record:
            ctx.say("There is nothing waiting to be paid for.")
            return
        tg_purchase.mark_failed(record["purchase_id"], "cancelled")
        ctx.say("Dropped. Nothing was charged.")

    @router.fallback
    def _pasted(ctx):
        """
        A message with no command in it.

        Two things arrive this way and both are worth catching. A signature,
        because somebody has just come back from their wallet with it on the
        clipboard and asking them to prefix it with a command is how a
        purchase gets abandoned. And a contract address, because that is what
        gets typed all day in a chat full of traders.

        Everything else is ignored in silence. A bot that answers ordinary
        conversation is a bot that gets muted.
        """
        text = (ctx.args or "").strip()

        if looks_like_a_signature(text):
            # A signature is a payment, so it is only ever acted on in a direct
            # message, the same as the purchase it belongs to.
            if ctx.update.is_group:
                return
            wallet = tg_link.wallet_for(ctx.chat_id)
            if not wallet:
                ctx.say("That looks like a transaction signature, but no wallet "
                        "is linked here. Send /link first.")
                return
            ctx.wallet = wallet
            submit_signature(ctx, api, text)
            return

        if looks_like_a_mint(text):
            # Deliberately quiet in groups. A room where people paste addresses
            # constantly would get an offer every time, which is how a bot gets
            # removed from a chat.
            if ctx.update.is_group:
                return

            wallet = tg_link.wallet_for(ctx.chat_id)
            if not wallet:
                ctx.say(
                    "That looks like a token address. I can pull the holders, "
                    "the authorities and the known risks on it.\n\n"
                    "Link a wallet with /link and paste it again.")
                return

            ctx.wallet = wallet
            start_purchase(ctx, api, "contract-intel", text)

    @router.command("pending", help="What you have in flight.",
                    private_only=True, group="buy")
    def _pending(ctx):
        awaiting = tg_purchase.awaiting_for(ctx.chat_id)
        running = [r for r in tg_purchase.history(ctx.chat_id, limit=20)
                   if r["state"] == tg_purchase.SUBMITTED]

        if not awaiting and not running:
            ctx.say("Nothing in flight.")
            return

        lines = []
        if awaiting:
            lines.append(
                f"Waiting on payment: {awaiting['component']}, "
                f"{awaiting['price']} {awaiting['currency']}")
        for record in running:
            lines.append(f"Running: {record['component']}")
        ctx.say("\n".join(lines))

    return router

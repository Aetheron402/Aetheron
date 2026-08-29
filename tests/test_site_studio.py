"""
The studio: the page you build on, and the meter that says what it costs.

The bug worth a permanent guard here is the one that made the old dialog
unusable. Two hundred lines of javascript sat inside {% block title %}, which
renders into <title>, so every function the modal called was parsed as text and
none of them existed. The close button did nothing because closeSiteModal was
never defined.
"""

import pathlib
import re

import pytest


TEMPLATES = sorted(pathlib.Path("templates").glob("*.html"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_template_hides_a_script_inside_its_title(path):
    """
    A <script> inside {% block title %} never runs, and nothing about the page
    looks broken until a button is pressed. It cost a whole working modal once.
    """
    source = path.read_text()
    for block in re.findall(r"{%\s*block title\s*%}(.*?){%\s*endblock", source, re.S):
        assert "<script" not in block.lower(), (
            f"{path.name} has a script inside its title block, where it cannot run")
        assert len(block) < 300, (
            f"{path.name} has {len(block)} characters in its title block, which "
            "renders inside <title>")


def test_the_studio_is_a_page_of_its_own():
    from fastapi.testclient import TestClient
    import Aetheron

    response = TestClient(Aetheron.app).get("/build")
    assert response.status_code == 200
    html = response.text

    # The two halves the studio exists for.
    assert 'id="preview"' in html
    assert 'id="studio-form"' in html
    assert 'id="edit-total"' in html


def test_the_shop_sends_you_to_the_studio_rather_than_a_dialog():
    from fastapi.testclient import TestClient
    import Aetheron

    html = TestClient(Aetheron.app).get("/shop").text
    assert "/build" in html
    assert "modal-site-bg" not in html, "the dead dialog is still being rendered"


def test_nothing_is_charged_for_an_empty_change():
    """
    The meter reads zero until something is queued, and the route agrees: an
    empty revision is refused before it is priced.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    client = TestClient(Aetheron.app)
    response = client.post(
        "/api/site-builder/revise",
        headers={"X-USER-WALLET": "SomeWallet"},
        json={"project_id": "SITE-NOPE", "edits": []},
    )
    # Unknown project is caught first, which is itself the point: neither path
    # reaches a payment prompt.
    assert response.status_code in (400, 404)


def test_the_meter_and_the_charge_come_from_the_same_function():
    """
    The studio shows what /api/site-builder/quote returns and the revise route
    charges what pricing.revision_quote returns, and those have to be the same
    call or the meter is a lie.
    """
    source = open("Aetheron.py").read()

    route = source.split("def site_builder_revise")[1].split("\ndef ")[0]
    assert "pricing.revision_quote(" in route
    assert 'price = quote["price"]' in route

    quote_route = source.split("def site_builder_quote")[1].split("\ndef ")[0]
    assert "pricing.revision_quote(" in quote_route


def test_a_change_can_name_the_element_it_is_aimed_at():
    source = open("Aetheron.py").read()
    assert "class SiteEdit" in source
    route = source.split("def site_builder_revise")[1].split("\ndef ")[0]
    assert "In the element matching" in route


def test_the_preview_is_written_from_a_buffer_that_can_be_replayed():
    """
    Pub/sub drops anything published with nobody listening, so a reconnecting
    browser would see a page missing its first half.
    """
    source = open("site_stream.py").read()
    assert "rpush" in source and "lrange" in source
    assert "publish(" not in source


def test_a_build_survives_the_browser_leaving():
    """
    The stream is a convenience. The generation runs in the worker and stores
    its result regardless, because it was paid for.
    """
    source = open("celery_worker.py").read()
    task = source.split("def process_site_builder")[1].split("\n@celery")[0]
    assert "site_stream.begin" in task
    assert "store_asset" in task
    assert "site_stream.finish" in task


def test_a_failed_build_says_so_on_the_stream():
    """
    A stream that simply stops looks exactly like a slow one, and the person
    watching has already paid.
    """
    source = open("celery_worker.py").read()
    task = source.split("def process_site_builder")[1].split("\n@celery")[0]
    assert "site_stream.fail" in task
    assert "site_projects.fail" in task


def test_streaming_forwards_page_text_and_not_thinking():
    """
    Thinking deltas are not part of the page. Forwarding them would paint the
    model's reasoning into the preview and store it in the file.
    """
    source = open("llm.py").read()
    fn = source.split("def complete_streamed")[1].split("\ndef ")[0]
    assert 'getattr(event.delta, "type", None) == "text_delta"' in fn
    assert "stop_reason" in fn, "a refusal mid stream must still be caught"


def test_the_aeth_amount_comes_from_the_challenge_itself():
    """
    It used to price in dollars whatever method was asked for, which left the
    browser converting for itself against a cached rate for a different price.
    That put one figure on the button and another in the dialog, and settlement
    honours what the server locked, so the smaller would have been rejected as
    short after the money had gone.
    """
    source = open("Aetheron.py").read()
    body = source.split("def payment_required")[1].split("\ndef ")[0]
    assert "required_aeth" in body

    studio = open("templates/site_studio.html").read()
    assert "quote.required_aeth" in studio
    assert "aethAmount(aethFor(payMethodName))" not in studio


def test_the_price_is_on_the_button_rather_than_a_line_of_its_own():
    studio = open("templates/site_studio.html").read()

    assert 'id="studio-aeth"' not in studio, "the separate amount line is gone"
    assert "`Build it, ${aethAmount(aethBuild)}`" in studio
    assert "AETH`" in studio and "Apply " in studio


def test_changes_can_be_paid_for_in_either_currency_after_the_form_is_hidden():
    """
    The build form is hidden once a site exists, and its radios go with it, so
    without a selector in the edit panel there is no way to pay for a change in
    anything but the method chosen before the first build.
    """
    studio = open("templates/site_studio.html").read()

    assert 'name="pay_edit"' in studio
    assert '"pay_edit",' in studio, "the revise call must read its own selector"


def test_the_studio_pays_the_same_way_every_other_component_does():
    """
    402 with a wallet and an amount, send it on chain, paste the signature,
    resubmit with X-TX-SIG. No second mechanism, because a component that paid
    differently would need its own settlement path on the server.
    """
    studio = open("templates/site_studio.html").read()

    assert "res.status === 402" in studio
    assert '"X-TX-SIG": sig.trim()' in studio
    assert '"X-PAYMENT-METHOD": method' in studio
    assert '"X-USER-WALLET": window.currentWallet' in studio


# ── what a change costs ─────────────────────────────────────────────────────

def test_renaming_something_costs_a_fraction_of_a_restyle():
    """
    A flat price per change put the same number on rewriting a headline as on
    adding a whole section, which is not what either costs.
    """
    import pricing

    wording = pricing.revision_quote(
        [{"selector": "h1", "description": "change this to Grump Coin"}])["price"]
    look = pricing.revision_quote(
        [{"selector": "h1", "description": "make this red instead"}])["price"]
    page = pricing.revision_quote(
        [{"selector": None, "description": "add a how to buy section"}])["price"]

    assert wording < look < page
    assert wording <= 0.15, "renaming a heading should be close to nothing"


def test_a_batch_is_priced_as_one_job_because_it_is_one():
    """
    One revision is one generation call whatever it is asked to do, so five
    changes must not cost five times one.
    """
    import pricing

    one = pricing.revision_quote(
        [{"selector": "h1", "description": "change the name"}])["price"]
    five = pricing.revision_quote(
        [{"selector": "h1", "description": "change the name"}] * 5)["price"]

    assert five < one * 5
    assert five > one, "extra changes should still cost something"


def test_a_batch_never_costs_more_than_its_ceiling():
    """
    Otherwise the cheapest way to fix a page would be to rebuild it.
    """
    import pricing

    many = pricing.revision_quote(
        [{"selector": None, "description": "redesign the whole layout"}] * 20)

    assert many["capped"] is True
    assert many["price"] <= pricing.list_price("site-builder") * pricing.BATCH_CAP_SHARE
    assert many["price"] < pricing.list_price("site-builder")


def test_an_unclear_change_is_priced_in_the_buyer_s_favour():
    """
    These are keyword guesses about free text and will sometimes be wrong.
    Being wrong cheaply costs us cents. Being wrong the other way charges
    somebody five times over for renaming a heading.
    """
    import pricing

    assert pricing.classify_edit(
        {"selector": "h1", "description": "make this say something else"}) == "wording"


def test_a_change_with_nothing_pointed_at_is_page_wide():
    """
    It has to be applied by reading the whole file rather than one element of
    it, which is what page wide means here.
    """
    import pricing
    assert pricing.classify_edit(
        {"selector": None, "description": "make it friendlier"}) == "page"


def test_the_studio_never_works_the_price_out_itself():
    """
    Two implementations of a price disagree eventually, and the half that is
    wrong is either the number somebody was shown or the number they are
    charged.
    """
    studio = open("templates/site_studio.html").read()

    assert "/api/site-builder/quote" in studio
    assert "queued.length * EDIT_PRICE" not in studio
    assert "EDIT_PRICE" not in studio, "the flat per change price is gone"


def test_the_quote_endpoint_costs_nothing_to_ask():
    from fastapi.testclient import TestClient
    import Aetheron

    client = TestClient(Aetheron.app)
    response = client.post("/api/site-builder/quote", json={
        "project_id": "SITE-ANY",
        "edits": [{"selector": "h1", "description": "change the name"}]})

    assert response.status_code == 200
    body = response.json()
    assert body["price"] > 0
    assert body["tier_labels"] == ["wording"]


def test_the_page_no_longer_promises_a_flat_price_per_change():
    studio = open("templates/site_studio.html").read()
    assert "per change after" not in studio
    assert "priced by" in studio


# ── pointing at things ──────────────────────────────────────────────────────

def test_clicking_the_page_opens_a_box_where_you_clicked():
    """
    A panel on the far side of the screen makes somebody look away from the
    thing they are changing.
    """
    studio = open("templates/site_studio.html").read()

    assert 'id="spot"' in studio
    assert "function openSpot" in studio
    assert "getBoundingClientRect" in studio
    assert "addSpotEdit" in studio


def test_pointing_is_on_as_soon_as_there_is_a_page():
    """
    It is the whole reason the preview is there, so it should not need arming
    first.
    """
    studio = open("templates/site_studio.html").read()
    attach = studio.split("function attachPicker")[1].split("\nfunction ")[0]
    assert "togglePick(true)" in attach


def test_a_link_in_the_generated_page_cannot_navigate_the_preview_away():
    studio = open("templates/site_studio.html").read()
    assert "e.preventDefault()" in studio
    assert "e.stopPropagation()" in studio


def test_the_box_is_kept_inside_the_preview():
    """
    A click near an edge would otherwise open it off the side of the screen.
    """
    studio = open("templates/site_studio.html").read()
    assert "maxLeft" in studio and "maxTop" in studio


def test_the_preview_still_follows_the_page_as_it_scrolls():
    """
    Adding position:relative for the edit box overrode position:sticky, because
    it came later in the same stylesheet, and the preview stopped following the
    page. Sticky already anchors an absolutely positioned child, so the
    override was never needed.
    """
    import re

    studio = open("templates/site_studio.html").read()
    css = studio.split("<style>")[1].split("</style>")[0]
    # Comments explain the rule and mention the property, so they have to go
    # before the property itself can be counted.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    bodies = re.findall(r"\.studio-preview\s*\{([^}]*)\}", css)
    assert bodies, "the preview rule went missing entirely"

    positions = [d.split(":", 1)[1].strip()
                 for body in bodies for d in body.split(";")
                 if d.strip().startswith("position")]

    assert positions == ["sticky"], (
        f"the preview should be sticky and nothing else, got {positions}")


def test_dev_mode_does_not_quote_a_price_it_will_not_charge():
    """
    The bypass was enforced server side, so the page worked, but every button
    still said 2.50 USDC for something about to be free. A button quoting a
    price nobody is charged is a button telling a lie.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    client = TestClient(Aetheron.app)
    paid = client.get("/build").text
    assert "Build it, 2.50 USDC" in paid
    assert "Dev mode" not in paid
    assert 'name="pay_studio"' in paid

    if not Aetheron.DEV_TOKEN:
        return

    unlocked = client.get(
        "/build", headers={"X-DEV-TOKEN": Aetheron.DEV_TOKEN}).text
    assert "free in dev" in unlocked
    assert "Nothing is charged" in unlocked
    assert 'name="pay_studio"' not in unlocked, "a payment choice with nothing to pay"


def test_the_bypass_still_gives_nothing_away_when_it_is_off():
    """
    dev_mode reaching the template must not become a way to find out whether a
    token is set at all.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    html = TestClient(Aetheron.app).get("/build").text
    assert "DEV_TOKEN" not in html
    assert "dev/unlock" not in html


def test_the_token_in_the_url_unlocks_in_one_step():
    """
    Two steps, unlock then navigate, meant landing on a page of JSON and having
    to go somewhere else afterwards. One link should open the thing unlocked.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    if not Aetheron.DEV_TOKEN:
        return

    client = TestClient(Aetheron.app)
    response = client.get(f"/build?token={Aetheron.DEV_TOKEN}")

    assert response.status_code == 200
    assert "Dev mode. Nothing is charged" in response.text
    assert "Build it, 2.50 USDC" not in response.text

    # And it leaves the cookie, so the token does not have to ride along in
    # every later URL.
    assert "aetheron_dev" in response.headers.get("set-cookie", "")


def test_a_wrong_token_in_the_url_unlocks_nothing():
    from fastapi.testclient import TestClient
    import Aetheron

    if not Aetheron.DEV_TOKEN:
        return

    html = TestClient(Aetheron.app).get("/build?token=nearlyright").text
    assert "Dev mode" not in html
    assert "Build it, 2.50 USDC" in html


def test_a_token_in_the_url_does_nothing_when_none_is_set():
    """
    Inert without DEV_TOKEN, the same as every other way in.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    if Aetheron.DEV_TOKEN:
        return

    html = TestClient(Aetheron.app).get("/build?token=anything").text
    assert "Dev mode" not in html


# ── what the preview shows while it is thinking ─────────────────────────────

def test_the_preview_shows_it_is_working_from_the_moment_build_is_pressed():
    """
    A black rectangle for a minute reads as broken, and somebody who thinks it
    is broken presses build again, which is a second charge for a page they
    already bought.
    """
    studio = open("templates/site_studio.html").read()

    assert 'id="skeleton"' in studio
    assert 'id="progress"' in studio
    assert "function startWaiting" in studio

    # Before the request comes back, not after, since the wait before the first
    # character is where it looked deadest.
    build = studio.split("async function startBuild")[1].split("\n}")[0]
    assert build.index("startWaiting") < build.index("paidCall")


def test_the_skeleton_goes_once_there_is_something_real_to_look_at():
    """
    It should be watched being written, so the skeleton clears partway through
    rather than at the end.
    """
    studio = open("templates/site_studio.html").read()
    watch = studio.split("async function watch")[1]

    assert "clearSkeleton()" in watch
    assert 'd.status === "done"' in watch
    assert watch.index("clearSkeleton()") < watch.index('d.status === "done"')


def test_a_cancelled_build_stops_the_animation():
    """
    Nothing is coming, so the pane must not sit there pretending otherwise.
    """
    studio = open("templates/site_studio.html").read()
    build = studio.split("async function startBuild")[1].split("\n}")[0]
    assert 'stopWaiting("nothing built yet")' in build


def test_a_failed_build_stops_the_animation_too():
    studio = open("templates/site_studio.html").read()
    watch = studio.split("async function watch")[1]
    assert 'stopWaiting("failed")' in watch


def test_the_progress_bar_does_not_pretend_to_know_how_far_through_it_is():
    """
    The model does not report progress, so a bar showing a percentage would be
    inventing one.
    """
    studio = open("templates/site_studio.html").read()
    assert "Indeterminate" in studio

    # The bar is a css animation with a fixed width sweeping across. Nothing in
    # the script sets its width or a value on it, which is what a bar claiming
    # to know how far through it was would have to do.
    assert "progress.style.width" not in studio
    assert "progress\").value" not in studio
    assert "setProgress" not in studio


def test_the_animation_respects_reduced_motion():
    studio = open("templates/site_studio.html").read()
    assert "prefers-reduced-motion" in studio


def test_the_download_link_is_dead_while_a_version_is_being_written():
    """
    During a change the panel is still showing from the last version, so the
    link sits there live and pointing at the previous file. Clicking it then
    hands somebody the old page with nothing saying it is stale, and they would
    reasonably think their change had not worked.
    """
    studio = open("templates/site_studio.html").read()

    assert "function lockDownload" in studio
    assert "lockDownload(true)" in studio.split("function startWaiting")[1].split("\n}")[0]
    assert "lockDownload(false)" in studio.split("function stopWaiting")[1].split("\n}")[0]
    assert "Download, once this finishes" in studio


def test_the_download_link_has_no_href_until_there_is_a_file():
    """
    A link to # is a button that does nothing when pressed.
    """
    studio = open("templates/site_studio.html").read()
    assert '<a id="dl-link" href="#"' not in studio


def test_the_picker_attaches_to_the_document_that_is_on_screen():
    """
    srcdoc parses asynchronously. Attaching to contentDocument on the line
    after setting srcdoc reaches the document being replaced, so every listener
    is discarded the moment the new one loads and clicking the page does
    nothing at all. Verified in a browser: the old ordering received zero
    clicks.
    """
    studio = open("templates/site_studio.html").read()

    assert 'frame.addEventListener("load"' in studio
    assert "readyToPoint" in studio
    # Once per document, since streaming replaces it repeatedly.
    assert "__aetheronPicker" in studio


def test_the_load_listener_is_registered_without_waiting_for_an_event():
    """
    The script sits below the iframe, so the element exists. Waiting for
    DOMContentLoaded risks registering nothing at all if it has already fired.
    """
    studio = open("templates/site_studio.html").read()

    # The comment above the listener names the event, so look for the actual
    # registration rather than the word.
    assert 'addEventListener("DOMContentLoaded"' not in studio

    # And the iframe really is above the script that reaches for it.
    assert studio.index('id="preview"') < studio.index("let readyToPoint")


# ── coming back to a site you already built ─────────────────────────────────

def test_a_site_can_be_opened_again_rather_than_only_downloaded():
    """
    The list offered a download and nothing else, so coming back to change
    something meant building the whole page again at full price, which is the
    thing revisions exist to avoid.
    """
    studio = open("templates/site_studio.html").read()

    assert "async function openProject" in studio
    assert ">open</button>" in studio
    assert "open one to keep working on it" in studio


def test_opening_a_site_makes_it_the_one_changes_apply_to():
    """
    Otherwise a change queued after opening an old site would be applied to
    whichever project happened to be loaded before.
    """
    studio = open("templates/site_studio.html").read()
    fn = studio.split("async function openProject")[1].split("\n}")[0]

    assert "projectId = id" in fn
    assert "currentFile = filename" in fn
    assert "onBuilt()" in fn


def test_opening_a_site_drops_anything_queued_against_the_last_one():
    """
    A queue carried across pages applies somebody's changes to the wrong site.
    """
    studio = open("templates/site_studio.html").read()
    fn = studio.split("async function openProject")[1].split("\n}")[0]

    assert "queued = []" in fn
    assert "closeSpot()" in fn


def test_an_earlier_version_can_be_opened_too():
    """
    A revision that came out worse is only recoverable if the version before it
    can be loaded back, not just downloaded.
    """
    studio = open("templates/site_studio.html").read()
    assert "open an earlier one" in studio


def test_a_version_that_is_no_longer_stored_says_so():
    studio = open("templates/site_studio.html").read()
    fn = studio.split("async function openProject")[1].split("\n}")[0]
    assert "no longer stored" in fn
    assert 'stopWaiting("could not open")' in fn


# ── being told what was wrong ───────────────────────────────────────────────

def test_every_field_stops_at_the_length_the_server_accepts():
    """
    Without a cap the request goes out, comes back 422, and the person is left
    looking at a page that appears to have done nothing.
    """
    import re

    import Aetheron

    studio = open("templates/site_studio.html").read()
    limits = {
        "f-name": 80, "f-symbol": 20, "f-image": 400,
        "f-twitter": 200, "f-telegram": 200, "f-mint": 44,
        "f-description": 600, "f-notes": 1200,
    }

    for field, expected in limits.items():
        match = re.search(rf'id="{field}"[^>]*maxlength="(\d+)"', studio) \
            or re.search(rf'id="{field}"[^>]*?maxlength="(\d+)"', studio)
        assert match, f"{field} has no maxlength, so it can be refused after the request"
        assert int(match.group(1)) == expected, (
            f"{field} allows {match.group(1)} but the server takes {expected}")


def test_the_server_and_the_form_agree_on_those_limits():
    """
    Two numbers that can drift apart, and when they do the form lets somebody
    type something the server will refuse.
    """
    import Aetheron

    fields = Aetheron.SiteIn.model_fields
    assert fields["name"].metadata[0].max_length == 80
    assert fields["symbol"].metadata[0].max_length == 20
    assert fields["description"].metadata[0].max_length == 600
    assert fields["image"].metadata[0].max_length == 400
    assert fields["notes"].metadata[0].max_length == 1200


def test_a_validation_failure_is_turned_into_a_sentence():
    """
    FastAPI answers with a list of objects. Passing it to alert prints
    [object Object], which tells somebody nothing at all.
    """
    studio = open("templates/site_studio.html").read()

    assert "function readError" in studio
    assert "Array.isArray(detail)" in studio
    # And it names the field in the words on the form, not the wire name.
    assert '"Anything specific you want"' in studio
    assert "alert(readError(data))" in studio


def test_the_error_reader_never_shows_object_object():
    """
    Checked by running it rather than reading it, since that is the failure.
    """
    import json
    import re
    import subprocess

    studio = open("templates/site_studio.html").read()
    fn = "function readError" + studio.split("function readError")[1]
    fn = fn[:fn.index("\n}") + 2]

    cases = [
        {"detail": [{"loc": ["body", "notes"], "msg": "String should have at most 1200 characters"}]},
        {"detail": "plain string reason"},
        {"error": "an error key"},
        {},
    ]
    script = fn + "\n" + "\n".join(
        f"console.log(readError({json.dumps(c)}));" for c in cases)

    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if out.returncode != 0:
        return  # node is not installed here, the source checks above still hold

    lines = out.stdout.strip().split("\n")
    assert "[object Object]" not in out.stdout
    assert "Anything specific you want" in lines[0]
    assert lines[1] == "plain string reason"
    assert lines[2] == "an error key"
    assert "nothing was charged" in lines[3]


def test_a_rejected_request_is_logged_by_field_and_never_by_value(capsys):
    """
    Diagnosing a 422 meant guessing, because the browser console shows the
    status and nothing else. The log now names the field and the length. It
    must not carry the value: these bodies hold wallet addresses and whatever
    somebody wrote about their project.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    secret = "SECRET" * 40
    response = TestClient(Aetheron.app).post(
        "/api/site-builder", json={"name": secret, "symbol": "x"},
        headers={"X-USER-WALLET": "W" * 40})

    assert response.status_code == 422
    logged = capsys.readouterr().out

    assert "name(string_too_long, 240 chars)" in logged
    assert secret not in logged, "the rejected value was written to the log"


def test_a_rejected_request_carries_a_sentence_as_well_as_the_detail():
    """
    The list of objects stays, because clients read it. The sentence is what a
    person can actually be shown.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    body = TestClient(Aetheron.app).post(
        "/api/site-builder", json={"name": "x" * 200, "symbol": "y"},
        headers={"X-USER-WALLET": "W" * 40}).json()

    assert isinstance(body["detail"], list)
    assert "at most 80 characters" in body["message"]


def test_ghost_buttons_are_actually_styled():
    """
    base.html carried a .btn-ghost:hover rule and no .btn-ghost rule, so every
    ghost button across the site rendered as bare text until a cursor landed on
    it. The studio uses them for download, discard and start another.
    """
    import re

    css = open("templates/base.html").read()
    selectors = [m.group(1).strip()
                 for m in re.finditer(r"([^{}\n]*\.btn-ghost[^{}]*)\{", css)]

    assert ".btn-ghost" in selectors, (
        f"only these rules mention it: {selectors}")


# ── the phone ───────────────────────────────────────────────────────────────

def test_the_studio_is_refused_on_a_phone_rather_than_half_offered():
    """
    Editing works by clicking one exact element inside the preview. On a phone
    that is a broken feature rather than a smaller one, and paying then finding
    you cannot change anything is the worst outcome this has.
    """
    studio = open("templates/site_studio.html").read()
    css = studio.split("<style>")[1].split("</style>")[0]

    assert ".studio-mobile" in css
    assert "max-width: 900px" in css
    # The studio itself is hidden at that width, not merely restyled.
    narrow = css.split("@media (max-width: 900px)")[1].split("}")[1]
    assert ".studio { display: none" in css.split("@media (max-width: 900px)")[1][:200]


def test_the_phone_screen_explains_rather_than_only_refusing():
    """
    Somebody who arrives on a phone should leave knowing what it does, not just
    that they cannot have it.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    html = " ".join(TestClient(Aetheron.app).get("/build").text.split())

    assert "This one needs a desktop" in html
    assert "reads your token itself" in html
    assert "Copy the link" in html
    # And a way onward rather than a dead end.
    assert "Browse the rest" in html


def test_the_preview_can_be_flipped_to_phone_width():
    """
    Whoever is building is on a desktop by definition now, while almost everyone
    who opens their link will be on a phone.
    """
    studio = open("templates/site_studio.html").read()

    assert "function togglePhone" in studio
    assert '"390px"' in studio
    # Narrowed rather than reloaded, so a build in progress keeps streaming.
    fn = studio.split("function togglePhone")[1].split("\n}")[0]
    assert "srcdoc" not in fn and "reload" not in fn


# ── the four studio additions ───────────────────────────────────────────────

def test_the_address_can_be_filled_in_without_a_model_call():
    """
    Launch day otherwise means opening the file in a text editor and finding
    the line. It is a string replacement, so it is instant and free, and
    handing it to a model would mean paying for a generation and waiting a
    minute to change forty characters.
    """
    import site_patch

    page = '<html><script>\n  const CONTRACT_ADDRESS = "";\n</script>x</html>'
    out = site_patch.set_contract_address(page, "D3qncuGsa2iMKcaxnqZxUMeVqPztzyAr819nXfjypump")

    assert "D3qncuGsa2iMKcax" in out
    # Nothing else moved.
    assert out.replace("D3qncuGsa2iMKcaxnqZxUMeVqPztzyAr819nXfjypump", "") == page


def test_the_address_fill_refuses_a_page_that_has_no_line_for_it():
    import site_patch
    import pytest as _pytest

    with _pytest.raises(site_patch.PatchError) as exc:
        site_patch.set_contract_address("<html>nothing here</html>", "ABC")
    assert "no contract address line" in str(exc.value)


def test_the_address_fill_route_charges_nothing():
    source = open("Aetheron.py").read()
    route = source.split("def site_builder_address")[1].split("\ndef ")[0]

    assert "verify_payment" not in route, "filling in an address must be free"
    assert "price=0.0" in route
    assert "owned_by" in route, "and still only for the wallet that owns it"


def test_a_reroll_walks_to_a_different_design():
    """
    Rehashing with a salt could return the same look twice in a row, which
    reads as the button being broken.
    """
    import site_data

    seen = [site_data.direction_for("SomeMint", i)["name"] for i in range(8)]
    assert len(set(seen)) == 8
    assert site_data.direction_for("SomeMint", 8)["name"] == seen[0]


def test_the_first_reroll_is_free_and_the_next_is_not():
    source = open("Aetheron.py").read()
    route = source.split("def site_builder_reroll")[1].split("\ndef ")[0]

    assert "used < 1" in route
    assert "if not free:" in route
    assert "verify_payment" in route


def test_not_knowing_how_many_rerolls_charges_rather_than_gives_one_away():
    """
    The count guards the free one, so a failure to read it has to fall the
    paying way.
    """
    source = open("site_projects.py").read()
    fn = source.split("def rerolls_used")[1].split("\ndef ")[0]
    assert "return 1" in fn.split("except Exception:")[1]


def test_a_reroll_stays_on_the_same_project():
    """
    Otherwise asking for a different look leaves two sites in the list with the
    older one unreachable from the newer.
    """
    source = open("celery_worker.py").read()
    task = source.split("def process_site_builder")[1].split("\n@celery")[0]
    assert "if project_id:" in task
    assert "site_projects.start" in task


def test_the_page_carries_link_preview_tags():
    """
    These get posted on X, and a bare url with no card is the difference
    between a link people open and one they scroll past.
    """
    brief = open("celery_worker.py").read()
    flat = " ".join(brief.split())

    assert "og:title" in flat and "twitter:card" in flat
    assert "leave og:image out entirely rather than pointing it at nothing" in flat


def test_a_pre_launch_page_can_pick_up_market_figures_later():
    """
    A page built before launch never had numbers, so it would never show any,
    even once the token is trading. Real data read at page load, so it does not
    break the rule against inventing.
    """
    brief = open("celery_worker.py").read()
    flat = " ".join(brief.split())

    assert "api.dexscreener.com" in flat
    assert "pair with the most liquidity" in flat
    assert "Never render a zero, a dash or a loading state in place of a figure" in flat


def test_the_project_id_is_taken_from_the_stream_not_the_response():
    """
    The worker creates the project, so the build request cannot return its id.
    Reading it from the response left it null after every fresh build, and every
    edit, section rewrite, reroll and address fill then failed with nothing to
    change. Only opening an existing site worked, which is why it survived
    testing.
    """
    studio = open("templates/site_studio.html").read()
    watch = studio.split("async function watch")[1]

    assert "if (d.project_id) projectId = d.project_id;" in watch
    assert "res.project_id" not in studio


# ── one number, and the server decides it ───────────────────────────────────

def test_the_challenge_carries_the_aeth_amount_when_paying_in_aeth():
    """
    It priced in dollars whatever was asked for, so the browser worked the
    conversion out itself. A browser doing that against a cached rate for a
    different price showed one figure on the button and another in the dialog.
    """
    source = open("Aetheron.py").read()
    fn = source.split("def payment_required")[1].split("\ndef ")[0]

    assert 'body["required_aeth"] = required_aeth' in fn
    assert 'pricing.effective_usd(price_usdc, wallet, "AETH")' in fn


def test_the_locked_quote_is_for_the_amount_actually_charged():
    """
    Settlement measures the transfer against the locked quote. Locking the
    component's list price while charging a fraction of it meant a correct
    payment came back short, after the money had gone.
    """
    fn = open("Aetheron.py").read().split("def payment_required")[1].split("\ndef ")[0]

    assert "aeth_quotes.record(wallet, component," in fn
    assert "int(round(required_aeth * (10 ** decimals)))" in fn
    assert "is how a part charge came to expect a full one" in " ".join(fn.split())


def test_every_challenge_knows_which_currency_was_asked_for():
    """
    A challenge quoted in dollars for somebody paying in AETH is the whole bug.
    """
    import re

    source = open("Aetheron.py").read()
    calls = [c for c in re.findall(r"payment_required\((?:[^()]|\([^()]*\))*?\)", source)
             if "component: str" not in c]

    assert len(calls) >= 8
    for call in calls:
        assert "payment_method" in call, " ".join(call.split())[:90]


def test_the_studio_shows_what_the_server_said_rather_than_its_own_sum():
    studio = open("templates/site_studio.html").read()
    fn = studio.split("async function paidCall")[1].split("\n}")[0]

    assert "quote.required_aeth" in fn
    assert "aethAmount(aethFor(payMethodName))" not in fn


def test_the_button_scales_the_same_way_the_server_charges():
    """
    The button has to show something before there is a challenge to read, so it
    scales the quoted ceiling. Scaling off the discounted dollar figure instead
    applied the AETH tier twice on one side and not at all on the other.
    """
    import pricing

    list_price = pricing.list_price("site-revision")
    ceiling = pricing.effective_usd(list_price, None, "AETH")

    batch = pricing.revision_quote(
        [{"selector": "h1", "description": "change the name"}])["price"]
    owed = pricing.effective_usd(batch, None, "AETH")

    button_share = batch / list_price
    server_share = owed / ceiling

    assert abs(button_share - server_share) < 0.01, (
        f"button scales by {button_share:.4f}, server charges {server_share:.4f}")

    studio = open("templates/site_studio.html").read()
    assert "aethEdit.list_price" in studio

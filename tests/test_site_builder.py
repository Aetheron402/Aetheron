

def test_the_brief_forbids_em_dashes():
    """
    Buyers publish these pages under their own name, and an em dash is the
    fastest way for a page to read as machine written. The first generated page
    used them as separators throughout, so the brief says not to.
    """
    source = open("celery_worker.py").read()
    assert "Never use an em dash" in source


def test_the_brief_demands_full_width_backgrounds():
    """
    The first generated page put the hero gradient on the same element that
    carried max-width:1080px, so the colour stopped at the text column and left
    bare strips down both sides on a wide screen. Structural, so it would have
    recurred on every page until the brief said not to.
    """
    source = open("celery_worker.py").read()
    assert "Every background runs the full width of the window" in source
    assert "max-width and a background on the same element" in source


def test_a_revision_edits_rather_than_regenerates():
    """
    Somebody paying to change a headline must not be handed a different site.
    The previous file goes into the prompt and everything unmentioned has to
    come back unchanged.
    """
    source = open("celery_worker.py").read()
    task = source.split("def process_site_revision")[1]

    assert "latest_file" in task, "the revision must load the page it is editing"
    assert "This is an edit, not a rebuild" in task
    assert "Here is the page as it stands" in task


def test_a_revision_costs_less_than_a_build():
    import pricing
    assert pricing.list_price("site-revision") < pricing.list_price("site-builder")


def test_a_revision_refuses_when_the_old_page_cannot_be_read():
    """
    Assets are purged after the retention window. Rebuilding from nothing and
    calling it a revision would hand the buyer a different page than the one
    they paid to change, so it has to say so instead.
    """
    source = open("celery_worker.py").read()
    task = source.split("def process_site_revision")[1]
    assert "no longer stored" in task


def test_reading_an_asset_back_works_on_both_backends():
    """
    fetch_asset returns None under R2 by design. A revision reading through it
    would silently rebuild from nothing on any deployment using R2.
    """
    source = open("storage.py").read()
    assert "def load_asset_text" in source
    helper = source.split("def load_asset_text")[1].split("\ndef ")[0]
    assert "using_r2()" in helper and "R2_PUBLIC_BASE" in helper


def test_details_typed_into_a_revision_reach_the_page():
    """
    A revision that supplied a Telegram link came back without one: the rule
    about changing only what was asked for beat a fact sitting in a reference
    list. Anything typed into those fields is part of the request.
    """
    source = open("celery_worker.py").read()
    task = source.split("def process_site_revision")[1]
    assert "they are part of the " in task and "request" in task
    assert "reference only" in task, "unsupplied facts must stay reference only"


def test_the_brief_requires_a_fallback_for_images_that_fail():
    """
    Token images are pasted by hand and point at IPFS gateways or social media,
    which go down, block hotlinking, or are simply wrong. A generated page shipped
    a broken image icon with its alt text sitting in a styled frame, which is the
    most visible way one of these can embarrass whoever published it.
    """
    source = open("celery_worker.py").read()
    assert source.count("must carry an onerror") == 2, (
        "both the build brief and the revision brief need this, or an edit can "
        "reintroduce a broken image")


def test_every_page_is_built_from_the_same_sections():
    """
    Structure was left to the model and came out different every time, so two
    buyers paying the same price got pages with nothing in common and no way to
    know beforehand what they would get. Which sections exist is fixed. Their
    order is a default, since a buyer who wants how to buy near the top knows
    their audience better than a list in a prompt does.
    """
    source = open("celery_worker.py").read()
    brief = source.split("def process_site_builder")[1].split("\n@celery")[0]

    for section in ("#hero", "#contract", "#about", "#how-to-buy",
                    "#market", "#links", "#footer"):
        assert section in brief, f"{section} missing from the brief"

    # The list is a ceiling as well as a floor, or invented sections come back.
    assert "No other top level sections" in brief
    assert "no roadmap" in brief.lower() and "no team" in brief.lower()


def test_how_to_buy_is_allowed_but_cannot_invent_specifics():
    """
    A page for people who need telling how to buy, that does not tell them, is
    not doing its job. Steps are procedure rather than claims, so they are
    allowed, but fees and prices are still not ours to state.
    """
    source = open("celery_worker.py").read()
    brief = source.split("def process_site_builder")[1].split("\n@celery")[0]
    assert "Do not state fees, slippage, amounts, prices" in brief


def test_the_market_section_only_exists_when_there_are_figures():
    source = open("celery_worker.py").read()
    brief = source.split("def process_site_builder")[1].split("\n@celery")[0]
    assert "Never a placeholder, never a zero, never soon" in brief


def test_a_scrolling_ticker_cannot_ship_with_a_gap():
    """
    A generated page duplicated its phrases once and animated to -50%, which is
    correct only when the two copies together are wider than the window. Six
    short phrases were not, so the strip played the text and then scrolled
    emptiness.
    """
    source = open("celery_worker.py").read()
    assert source.count("min-width:200vw") == 2, (
        "the build brief and the revision brief both need this")
    assert "scrolls emptiness" in source


def test_the_spine_never_appears_as_visible_copy():
    """
    Giving the brief a numbered list of sections made a page print Section 01,
    Section 02 and so on down the side, which reads as a form somebody filled
    in. The structure is a skeleton, not wording.
    """
    source = open("celery_worker.py").read()
    assert "never the wording" in source
    assert "no Section 01" in source
    # the revision brief needs it too, or an edit can put the labels back
    assert source.count("structure, not copy") == 1
    assert "Never print the section names or numbers" in source


def test_the_brief_sets_a_contrast_floor_it_can_act_on():
    """
    Accessible contrast was too vague to hold. One page rendered body text that
    peaked at RGB 61 on a background of 8, which is unreadable, because the
    direction asked for restraint and nothing said where restraint stops.
    """
    source = open("celery_worker.py").read()
    assert "60 percent of the way from its background to white" in source
    # collapsed, because the brief is wrapped and the phrase spans two lines
    flat = " ".join(source.split())
    assert "Restraint comes from space, type and colour choice" in flat
    assert "lowering text contrast" in source, "revisions must not undo it either"


def test_narrow_columns_have_to_be_centred_too():
    """
    A centred 960px wrapper holding uncentred 520px columns strands every line
    against one edge with half the screen empty.
    """
    source = open("celery_worker.py").read()
    assert "not left in place inside a wider centred wrapper" in source


def test_a_revision_asks_for_the_change_before_the_page():
    """
    Rewriting the whole document to alter one headline wrote thirteen thousand
    characters to change forty of them, and output is the expensive half of a
    call.
    """
    source = open("celery_worker.py").read()
    task = source.split("def process_site_revision")[1]

    assert "PATCH_PROMPT" in task
    assert task.index("llm.complete([PATCH_PROMPT]") < task.index("complete_streamed")


def test_a_revision_still_works_when_the_patch_cannot_be_trusted():
    """
    Slower and dearer, but it always works, which matters more than saving
    tokens on somebody who has already paid.
    """
    source = open("celery_worker.py").read()
    task = source.split("def process_site_revision")[1]

    assert "site_patch.PatchError" in task
    assert "falling back to a rewrite" in task
    assert "complete_streamed" in task, "the rewrite path has to still exist"


def test_the_patch_brief_demands_a_unique_match():
    source = open("celery_worker.py").read()
    brief = source.split("PATCH_PROMPT")[1].split('""".strip()')[0]

    assert "must appear exactly once" in brief
    assert "character for character" in brief
    assert "as short as it can be" in brief


def test_a_section_can_be_moved_when_the_owner_asks():
    """
    A buyer paid, asked for how to buy near the top, and was silently ignored
    because the brief pinned the order absolutely. The order is a default now.
    """
    source = open("celery_worker.py").read()
    flat = " ".join(source.split())

    assert "The order below is the default, not a rule" in flat
    assert "they know their audience better than this list does" in flat
    assert "#hero` stays first and `#footer` stays last" in flat


def test_moving_a_section_is_not_an_opening_to_add_one():
    """
    The whole reason the set is closed is that a roadmap or a team has no facts
    behind it. Loosening the order must not loosen that.
    """
    source = open("celery_worker.py").read()
    flat = " ".join(source.split())

    assert "reordering is not an opening to add one" in flat
    assert "asking for one directly does not change that" in flat
    assert "a request to invent one" in flat


def test_a_revision_may_reorder_but_still_may_not_invent():
    source = open("celery_worker.py").read()
    revision = source.split("You are changing one HTML page")[1]

    assert "Reordering them is allowed" in revision
    assert "is still refused" in revision


def test_the_brief_asks_for_motion_rather_than_only_permitting_it():
    """
    It only ever said to respect reduced motion if you animate anything, which
    is a constraint on motion and not a request for it. Pages came out with
    zero keyframes and one hover transition, which reads as a screenshot.
    """
    source = open("celery_worker.py").read()
    flat = " ".join(source.split())

    assert "should feel alive when it opens" in flat
    assert "reveal each section as it comes into view" in flat
    assert "reads as a screenshot" in flat


def test_motion_is_bounded_so_it_cannot_get_in_the_way():
    source = open("celery_worker.py").read()
    flat = " ".join(source.split())

    assert "Nothing bounces" in flat
    assert "delays text by more than about a third of a second" in flat


def test_a_reveal_cannot_make_the_page_unreadable():
    """
    A fade that starts at opacity zero in the stylesheet leaves a blank page if
    the script never runs.
    """
    source = open("celery_worker.py").read()
    flat = " ".join(source.split())

    assert "applied by script rather than sat in the stylesheet" in flat
    assert "invisible when a script fails" in flat


def test_reduced_motion_turns_all_of_it_off():
    source = open("celery_worker.py").read()
    flat = " ".join(source.split())
    assert "Under prefers-reduced-motion, all of it is off" in flat


def test_a_social_link_is_never_used_as_an_image():
    """
    A generated page put the token's x.com address in an <img src>. The onerror
    then removed the broken image, so the page looked finished while a supplied
    social had silently disappeared from it entirely.
    """
    source = open("celery_worker.py").read()
    flat = " ".join(source.split())

    assert "A social link is a link and never a picture" in flat
    assert "silently disappeared" in flat
    assert "Every social that was supplied appears in `#links`" in flat


def test_emoji_are_the_owners_call_rather_than_ours():
    """
    There was no rule at all, so a page got emoji only because the buyer asked.
    That is the right outcome, but it should be a decision rather than an
    absence: emoji are the fastest way for a page to read as generated.
    """
    source = open("celery_worker.py").read()
    flat = " ".join(source.split())

    assert "No emoji unless the owner asked for them" in flat
    assert "do not ration them" in flat
    # and a revision cannot sprinkle them in unasked
    assert "Do not add emoji unless the request asks for them" in flat

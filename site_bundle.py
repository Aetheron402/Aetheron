"""
The page, packaged so somebody knows what to do with it.

A buyer gets one HTML file, which is exactly right technically and leaves a
good number of them holding something they cannot use. They do not have a host,
they have not deployed anything before, and the file sits in Downloads.

So the same page also comes as a small zip: the page renamed to index.html,
which is what every host looks for, and one page of instructions covering the
three easiest ways to put it online. Nothing is added to the page itself.

Built here rather than by the model. It is the same three paragraphs every time
and there is no reason to pay for them.
"""

import io
import zipfile

README = """# Your site

`index.html` is your whole website. Everything is inside it: the styling, the
fonts, the script. There is nothing to install and nothing to build.

Open it by double clicking. It works with no internet.

## Putting it online

Any of these take a couple of minutes and cost nothing.

**Netlify Drop.** Go to app.netlify.com/drop and drag this folder onto the
page. You get a live address straight away. Sign up afterwards if you want to
keep it.

**Cloudflare Pages.** pages.cloudflare.com, create a project, upload the
folder. Free, and it stays fast when a lot of people arrive at once.

**GitHub Pages.** If you already use GitHub: new repository, upload
`index.html`, then Settings, Pages, and set the source to your main branch.

Any of them will give you a web address. Point your domain at it later if you
buy one.

## After you launch

{launch}

## Changing it

Come back to the studio, open this site, and click on whatever you want
different. You do not need to edit the file by hand.

Every version you have built stays available, so nothing you liked is lost.
"""

LAUNCH_PENDING = """Near the top of the `<script>` at the bottom of the file
there is one line:

    const CONTRACT_ADDRESS = "";

Put your address between the quotes and save. That single edit fills the
address in everywhere, shows the copy button, points the buy button at your
pump.fun page and removes every line saying the token has not launched.

You can also do it from the studio, which is one button and does the same
thing."""

LAUNCH_DONE = """Your contract address is already on the page.

If it ever changes, the studio can put a new one in for you."""


def readme_for(launched: bool) -> str:
    return README.format(launch=LAUNCH_DONE if launched else LAUNCH_PENDING)


def build(html: str, launched: bool = False) -> bytes:
    """
    Zip the page and the instructions.

    Named index.html because that is what a host serves without being asked,
    and the most common way this goes wrong is a file called something else
    sitting in a bucket doing nothing.
    """
    if not html:
        raise ValueError("There is no page to package")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html)
        archive.writestr("README.md", readme_for(launched))
    return buffer.getvalue()


def is_launched(html: str) -> bool:
    """
    Whether the address has been filled in, so the instructions match the page.

    Telling somebody to fill in a line that is already filled in reads as the
    instructions belonging to a different file.
    """
    import site_patch

    found = site_patch.CONTRACT_LINE.search(html or "")
    if not found:
        # No placeholder at all means it was built with the address in it.
        return True
    return bool(found.group(3).strip())

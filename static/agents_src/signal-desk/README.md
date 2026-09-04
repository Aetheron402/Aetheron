# Signal Desk

A publisher, not a watcher.

Every other agent finds something and prints it. Some of them fire a webhook
with a line of text in it, and that line is what gets a channel muted. This one
sits above them: it takes signals from wherever they come from, decides which
are worth posting and when, draws each one as a card, and posts it to Telegram
or Discord.

Point your other agents at its inbox and they stop being loggers and start
being a feed people read.

## What it does

**Draws.** Every signal becomes an image: title, the lines you gave it, a row
of facts, the mint, and your name at the bottom. Tone sets the accent colour so
good news and bad news do not look the same in a fast moving chat.

**Decides.** The half that matters. A room will forgive four good posts a day
and will mute forty.

- Nothing gets posted twice inside the cooldown, so the same token arriving
  from two agents is still one post.
- Nothing above your posts per hour limit.
- Nothing during quiet hours.
- Nothing below the score you set.
- Everything held back is kept and comes out later as one digest, so being
  quiet never means losing things.

**Posts.** Telegram, Discord, or both at once. A channel that fails does not
stop the others.

## Running it

    pip install -r requirements.txt
    python main.py

Fill in `config.json` first. With no channels enabled it logs what it would
have posted, which is a good way to watch the editorial rules work before you
point it at a real room.

## Feeding it

One JSON object per line, appended to the file named in `inbox.file`:

    {"kind": "risk", "tone": "bad", "score": 9,
     "title": "Liquidity pulled on CHIMP",
     "lines": ["Top holder sold 41 percent of supply"],
     "facts": {"liquidity": "$4.2k", "holders": "812"},
     "mint": "CSLP8Vp7u9hrXQi7crPXqCp7BaJaG4JrNxqvR3jDpump",
     "link": "https://solscan.io/token/..."}

Only `title` is required. Everything else changes what the card looks like.

`tone` is one of `good`, `watch`, `bad` or `neutral`.

Anything that can append a line of JSON can feed this, including a shell
script, a cron job, or any of the other agents.

There are two other inbox modes. `folder` reads a file per signal and deletes
it once posted, which suits scripts. `http` polls a URL that returns a list.

## Settings worth knowing

`start_at` is `end` by default, so turning the desk on does not dump a week of
old signals into a channel. Set it to `beginning` when you are feeding it a
file you prepared.

`per_subject_cooldown_minutes` groups by mint where there is one, so two
sources reporting the same token count as the same story.

`digest_at` is the time of day held signals get published together. Leave it
blank to drop them instead.

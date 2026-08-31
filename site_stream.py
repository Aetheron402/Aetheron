"""
The pipe between a page being generated and the browser watching it.

The studio shows the site building itself, which means the worker doing the
generating and the web process talking to the browser have to share a running
buffer. Redis holds it, because both already talk to Redis and neither can hold
it in memory: they are different processes, and often different machines.

Written as an append only list rather than pub/sub on purpose. Pub/sub drops
anything published while nobody is listening, so a browser that reconnects, or
opens a second later than the job starts, would see a page missing its first
half. A list can be replayed from the beginning, so a reconnect catches up.

The buffer is a convenience, not the deliverable. The finished page is stored
the same way every other asset is, so losing this costs a live preview and
never costs the thing that was paid for.
"""

import json
import os
import time

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Long enough to survive a slow build and a reconnect, short enough that these
# do not accumulate. The asset itself outlives this by design.
BUFFER_TTL_SECONDS = int(os.getenv("SITE_STREAM_TTL", "1800"))

_client = None


def _redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def _keys(asset_id):
    return f"site:stream:{asset_id}", f"site:state:{asset_id}"


# How long the arguments of a paid build are kept. Much longer than the stream
# itself, because the point of them is recovery: somebody paid, the build did
# not survive, and the only alternative to re-running it is asking them to pay
# a second time for the same thing.
ARGS_TTL_SECONDS = int(os.getenv("SITE_ARGS_TTL", str(14 * 24 * 3600)))


def remember(asset_id, args):
    """Keep what a build was asked to make, so it can be made again."""
    try:
        _redis().set(f"site:args:{asset_id}", json.dumps(args),
                     ex=ARGS_TTL_SECONDS)
    except Exception:
        # Losing this costs a retry, not the build. Never worth failing a
        # request that has already been paid for.
        pass


def recall(asset_id):
    """What that build was asked to make, or None if it is no longer known."""
    try:
        raw = _redis().get(f"site:args:{asset_id}")
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


MAX_RETRIES = int(os.getenv("SITE_MAX_RETRIES", "3"))


def count_retry(asset_id):
    """
    Register a re-run and say whether it is allowed.

    A row that stays pending would otherwise be an unlimited supply of builds
    for one payment. Three is enough for a bad deploy and far short of being
    worth abusing.
    """
    try:
        r = _redis()
        key = f"site:retries:{asset_id}"
        used = r.incr(key)
        r.expire(key, ARGS_TTL_SECONDS)
        return used <= MAX_RETRIES
    except Exception:
        # A broker that cannot count is not a reason to refuse somebody the
        # build they already paid for.
        return True


def begin(asset_id):
    """Clear anything stale and mark this build as running."""
    chunks, state = _keys(asset_id)
    r = _redis()
    pipe = r.pipeline()
    pipe.delete(chunks)
    pipe.set(state, "running", ex=BUFFER_TTL_SECONDS)
    pipe.execute()


def push(asset_id, text):
    """Append a fragment for anybody watching."""
    if not text:
        return
    chunks, _ = _keys(asset_id)
    r = _redis()
    pipe = r.pipeline()
    pipe.rpush(chunks, text)
    pipe.expire(chunks, BUFFER_TTL_SECONDS)
    pipe.execute()


def finish(asset_id, filename=None, project_id=None):
    """
    Mark the build done, and say what it produced.

    The filename and the project ride along so a watcher can download the file
    and go straight on to changing it, without a second request to work out
    which site it was just handed.
    """
    _, state = _keys(asset_id)
    payload = json.dumps({"status": "done", "filename": filename,
                          "project_id": project_id})
    _redis().set(state, payload, ex=BUFFER_TTL_SECONDS)


def fail(asset_id, reason):
    """
    Mark the build failed, with something worth showing.

    A stream that simply stops looks identical to a slow one, and the person
    watching has already paid, so the reason has to travel.
    """
    _, state = _keys(asset_id)
    payload = json.dumps({"status": "error", "error": str(reason)[:700]})
    _redis().set(state, payload, ex=BUFFER_TTL_SECONDS)


def read_from(asset_id, index):
    """
    Everything buffered from `index` on, plus the current state.

    Returns (chunks, next_index, state). A watcher polls this, so reconnecting
    at index 0 replays the page from its first character.
    """
    chunks_key, state_key = _keys(asset_id)
    r = _redis()
    chunks = r.lrange(chunks_key, index, -1) or []
    state = r.get(state_key)
    return chunks, index + len(chunks), state


def wait_for_state(asset_id, timeout=2.0, poll=0.25):
    """Block briefly for a state change, so a watcher is not a busy loop."""
    _, state_key = _keys(asset_id)
    r = _redis()
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = r.get(state_key)
        if state and state != "running":
            return state
        time.sleep(poll)
    return r.get(state_key)

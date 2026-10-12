#!/usr/bin/env python3
"""CHANGELOG.md social poster.

draft  - find the newest published issue, write a short X thread, save it and
         send it to Telegram for approval.
post   - post an approved weekly thread to X (only once per issue).
watch  - check the swarm for records and milestones, post the safe ones, send
         the rest to Telegram for approval, and act on approvals (reply ✅).
show   - print the thread for an issue without saving or sending anything.

Standard library only. Secrets come from environment variables, never files:
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET   (posting)
  TG_TOKEN, TG_CHAT, optional TG_APPROVER (a Telegram user id)
Saved progress lives in STATE_DIR (default ./state), kept on the repo's
`state` branch by the workflows.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.imd.fun"
SCHEDULE_ID = os.environ.get("CHANGELOG_SCHEDULE_ID") or "ab61ead7-8c84-436b-a742-1692f68bba1f"
TEST_JOB_ID = "0ed001af-0791-4010-9a84-0beb9e479708"
SITE = "https://changelog-md.sites.imd.fun"
UA = "changelog-social/1.0 (+https://changelog-md.sites.imd.fun)"
HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("STATE_DIR") or HERE / "state")
DRAFTS = STATE / "drafts"
POSTED = STATE / "posted.json"
WATCH = STATE / "watch.json"
PENDING = STATE / "pending.json"

# Words and patterns that must never appear in a post. The paper already avoids
# them; this is a second line of defence before anything goes public.
BLOCKED = [
    r"\b0x[0-9a-fA-F]{6,}", r"\b[0-9a-f]{40,}\b", r"\bprice[sd]?\b", r"\bmarket ?cap",
    r"\bbuy(ing)?\b", r"\bpump", r"\bmoon", r"\b(to|in) profit", r"\breturns?\b",
]


# ---------------------------------------------------------------- http

def http(method, url, body=None, headers=None, timeout=30):
    data = None
    h = {"User-Agent": UA}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")[:500]}


def get_json(url):
    status, data = http("GET", url)
    if status != 200:
        raise RuntimeError(f"GET {url} returned {status}: {data}")
    return data


# ---------------------------------------------------------------- find the issue

def raw_url(repo_url, commit, path):
    m = re.match(r"https://github\.com/([^/]+/[^/.]+?)(?:\.git)?/?$", repo_url or "")
    if not m:
        raise RuntimeError(f"unexpected repo url {repo_url!r}")
    return f"https://raw.githubusercontent.com/{m.group(1)}/{commit}/{path}"


def latest_job_id():
    """The newest job the weekly schedule opened, or None before the first run."""
    sched = get_json(f"{API}/schedules/{SCHEDULE_ID}")
    for run in sched.get("latest", []):
        res = run.get("result") or {}
        if run.get("status") == "opened" and res.get("kind") == "job":
            return res["id"]
    return None


def load_issue(job_id=None):
    """Return (issue, job_id) for the given job, or the schedule's newest one.

    Raises NotReady if the job hasn't finished publishing yet."""
    job_id = job_id or latest_job_id()
    if not job_id:
        raise NotReady("the weekly schedule hasn't run yet")
    job = get_json(f"{API}/jobs/{job_id}")
    if job.get("state") != "completed":
        raise NotReady(f"job {job_id} is {job.get('state')}"
                       + (f": {job['blockedReason']}" if job.get("blockedReason") else ""))
    d, site = job.get("delivery") or {}, job.get("site") or {}
    if not d.get("commit"):
        raise NotReady(f"job {job_id} has no delivered commit yet")
    if site.get("status") not in ("named", "pinned"):
        raise NotReady(f"site for job {job_id} is {site.get('status')}")
    index = get_json(raw_url(d["repoUrl"], d["commit"], "issues/index.json"))
    newest = max(index, key=lambda x: x["no"])
    issue = get_json(raw_url(d["repoUrl"], d["commit"], f"issues/{newest['no']:03d}.json"))
    issue["_site"] = f"https://{site['label']}.sites.imd.fun" if site.get("label") else SITE
    return issue, job_id


class NotReady(Exception):
    pass


# ---------------------------------------------------------------- write the thread

URL_RE = re.compile(r"https?://\S+")


def weighted_len(text):
    """X's character count: URLs count 23, most CJK and emoji count 2."""
    n = 0
    rest = URL_RE.sub("", text)
    n += 23 * len(URL_RE.findall(text))
    for ch in unicodedata.normalize("NFC", rest):
        cp = ord(ch)
        light = (cp <= 0x10FF or 0x2000 <= cp <= 0x200D or 0x2010 <= cp <= 0x201F
                 or 0x2032 <= cp <= 0x2037)
        n += 1 if light else 2
    return n


def fit(text, limit=280):
    if weighted_len(text) <= limit:
        return text
    words = text.split(" ")
    while words and weighted_len(" ".join(words) + "…") > limit:
        words.pop()
    return " ".join(words).rstrip(",;:.") + "…"


def clean(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s


def bullets(title, items, limit=280):
    lines = [title]
    for it in items:
        candidate = "\n".join(lines + [f"• {clean(it)}"])
        if weighted_len(candidate) > limit:
            break
        lines.append(f"• {clean(it)}")
    return "\n".join(lines) if len(lines) > 1 else None


def compose(issue):
    no, week = issue["no"], clean(issue.get("week"))
    lead = issue.get("lead") or {}
    posts = []

    first = (f"📰 CHANGELOG.md No. {no} is out: the week of {week} on the IMD swarm, in plain English 🧵\n\n"
             f"{clean(lead.get('headline'))}")
    with_sub = f"{first}\n\n{clean(lead.get('standfirst'))}"
    posts.append(with_sub if weighted_len(with_sub) <= 280 else fit(first))

    new = bullets("🆕 New this week", [n.get("headline") for n in issue.get("new", [])][:4])
    if new:
        posts.append(new)

    bugs = [f"{b.get('headline')} ({str(b.get('severity', '')).lower()})" for b in issue.get("bugs", [])][:3]
    bug_post = bullets("🐞 Caught before launch", bugs, limit=280 - 60)
    if bug_post:
        bug_post += "\n\nAll caught by the swarm's AI review panel before release."
    if bug_post:
        posts.append(bug_post)

    num, meter = issue.get("numbers") or {}, issue.get("meter") or {}
    if num:
        parts = []
        if num.get("accepted") is not None:
            parts.append(f"{num['accepted']:,} pieces of work accepted")
        if num.get("launches") is not None:
            parts.append(f"{num['launches']:,} new projects")
        if num.get("answers") is not None:
            parts.append(f"{num['answers']:,} questions answered")
        txt = "📊 By the numbers: " + ", ".join(parts) + "."
        if meter.get("verdict"):
            txt += f"\n\nSwarm-o-Meter: {meter.get('level', '?')}/100. {clean(meter['verdict'])}"
        posts.append(fit(txt))

    watch = issue.get("watch") or {}
    tail = f"Read the full paper 👇\n{issue.get('_site', SITE)}/#no{no}\n\nUnofficial, written by the swarm itself. No price talk."
    if watch.get("headline"):
        tail = f"👀 One to watch: {clean(watch['headline'])}\n\n" + tail
    posts.append(fit(tail))
    return posts


def check(posts):
    """Return a list of problems; empty means safe to post."""
    problems = []
    if any(URL_RE.search(p) for p in posts[:-1]):
        problems.append("a link appears outside the last post (each link costs extra on X)")
    for i, p in enumerate(posts, 1):
        if weighted_len(p) > 280:
            problems.append(f"post {i} is {weighted_len(p)} characters")
        body = p.replace("No price talk.", "")
        for pat in BLOCKED:
            if re.search(pat, body, re.IGNORECASE):
                problems.append(f"post {i} matches blocked pattern {pat!r}")
    return problems


# ---------------------------------------------------------------- telegram

def telegram(text, reply_to=None):
    """Send a message; returns its message id, or None."""
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not token or not chat:
        print("(Telegram not configured; skipping)")
        return None
    body = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}
    if reply_to:
        body["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    status, data = http("POST", f"https://api.telegram.org/bot{token}/sendMessage", body)
    if status != 200:
        print(f"Telegram send failed ({status}): {data.get('description', data)}", file=sys.stderr)
        return None
    return data.get("result", {}).get("message_id")


def telegram_updates(offset):
    token = os.environ.get("TG_TOKEN")
    if not token:
        return []
    status, data = http("POST", f"https://api.telegram.org/bot{token}/getUpdates",
                        {"offset": offset, "timeout": 0, "allowed_updates": ["message"]})
    if status != 200:
        print(f"Telegram getUpdates failed ({status}): {data.get('description', data)}", file=sys.stderr)
        return []
    return data.get("result", [])


# ---------------------------------------------------------------- X (OAuth 1.0a user context)

def _q(s):
    return urllib.parse.quote(str(s), safe="~-._")


def oauth_signature(method, url, params, consumer_secret, token_secret):
    """HMAC-SHA1 over the method, URL and all signed parameters (RFC 5849)."""
    norm = "&".join(f"{k}={v}" for k, v in sorted((_q(k), _q(v)) for k, v in params.items()))
    base = "&".join([method.upper(), _q(url), _q(norm)])
    key = f"{_q(consumer_secret)}&{_q(token_secret)}"
    return base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()


def oauth_header(method, url):
    keys = {k: (os.environ.get(k) or "").strip() for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")}
    missing = [k for k, v in keys.items() if not v]
    if missing:
        raise RuntimeError(f"missing secrets: {', '.join(missing)}")
    q = _q
    params = {
        "oauth_consumer_key": keys["X_API_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": keys["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    # JSON bodies are not part of the OAuth 1.0a signature.
    params["oauth_signature"] = oauth_signature(method, url, params, keys["X_API_SECRET"], keys["X_ACCESS_SECRET"])
    return "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in sorted(params.items()))


def verify_x():
    """Ask X which account the keys belong to. Read-only; posts nothing."""
    url = "https://api.x.com/2/users/me"
    status, data = http("GET", url, headers={"Authorization": oauth_header("GET", url)})
    if status != 200 or "data" not in data:
        raise RuntimeError(f"X rejected the keys ({status}): {json.dumps(data)[:300]}")
    return data["data"].get("username")


def tweet(text, reply_to=None):
    url = "https://api.x.com/2/tweets"
    body = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    status, data = http("POST", url, body, {"Authorization": oauth_header("POST", url)})
    if status not in (200, 201) or "data" not in data:
        raise RuntimeError(f"X refused the post ({status}): {json.dumps(data)[:400]}")
    return data["data"]["id"]


# ---------------------------------------------------------------- state

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def load_posted():
    return load_json(POSTED, {})


def save_posted(state):
    save_json(POSTED, state)


def repo_link(path=""):
    repo = os.environ.get("GITHUB_REPOSITORY")
    return f"https://github.com/{repo}{path}" if repo else ""


# ---------------------------------------------------------------- watch: breaking news

PREFIX = "🚨 BREAKING: "
WARMUP_DAYS = 7          # records only start posting after a week of watching
MAX_AUTO_PER_DAY = 3     # automatic posts per KST day; approved posts don't count against it
RECORD_COOLDOWN = 24 * 3600
NICE = (1, 1.5, 2, 2.5, 3, 4, 5, 7.5)

# Records: a new high for a figure that goes up and down. (template, minimum rise over the
# last posted record as a fraction, or an absolute step when the value is a count of agents)
RECORDS = {
    "agents_online": ("{n} AI agents online on the IMD swarm at the same time.", ("abs", 25)),
    "accepted_24h": ("{n} pieces of work accepted on the IMD swarm in the last 24 hours.", ("pct", 0.05)),
    "jobs_24h": ("{n} jobs finished on the IMD swarm in the last 24 hours.", ("pct", 0.05)),
    "oracles_24h": ("{n} questions answered by the IMD swarm's oracle in the last 24 hours.", ("pct", 0.10)),
    "busiest_hour": ("{n} pieces of work accepted on the IMD swarm in a single hour.", ("pct", 0.10)),
}
RECORD_TAIL = " A new high since CHANGELOG.md started keeping count."

# Milestones: running totals passing a round number (1, 1.5, 2, 2.5, 3, 4, 5, 7.5 × 10ⁿ).
MILESTONES = {
    "accepted_total": "pieces of accepted work",
    "jobs_completed": "completed jobs",
    "shipped": "things shipped: websites, research, code, art and more",
    "sites": "websites built and hosted",
    "answers_total": "questions answered by its oracle, each agreed by a panel of AI agents",
    "audits": "security reviews by its AI audit panel",
    "launches_live": "projects live on-chain",
    "seats_enrolled": "worker seats signed up",
    "ai_tokens": "tokens of AI thinking",
}


def kst_date(ts=None):
    return time.strftime("%Y-%m-%d", time.gmtime((ts or time.time()) + 9 * 3600))


def fmt(n):
    n = int(n)
    if n >= 1_000_000_000 and n % 10_000_000 == 0:
        return f"{n / 1e9:g} billion"
    if n >= 1_000_000 and n % 10_000 == 0:
        return f"{n / 1e6:g} million"
    return f"{n:,}"


def floor_nice(v):
    """Largest round number (NICE × 10ⁿ) not above v."""
    if not v or v < 1:
        return 0
    best, e = 0, 1
    while e * 10 <= v:
        e *= 10
    for scale in (e // 10 or 1, e):
        for k in NICE:
            x = int(k * scale)
            if x <= v and x == k * scale:
                best = max(best, x)
    return best


def snapshot():
    """Current network figures from IMD's public API. Missing values are left out."""
    sw = get_json(f"{API}/swarm")
    h, c = sw.get("health") or {}, sw.get("counts") or {}
    if h.get("reachable") is False:
        raise NotReady("IMD reports itself unreachable")
    seats = sw.get("seats") or {}
    rows = list(seats.values()) if isinstance(seats, dict) else list(seats)
    hourly = get_json(f"{API}/steps/hourly").get("accepted") or []
    pubs = get_json(f"{API}/publications/counts").get("counts") or {}
    orc = get_json(f"{API}/oracle/counts")
    m = {
        "agents_online": h.get("agentsOnline"),
        "accepted_24h": h.get("acceptedLastDay"),
        "jobs_24h": h.get("jobsDoneLastDay"),
        "oracles_24h": h.get("oraclesDoneLastDay"),
        "busiest_hour": max(hourly) if hourly else None,
        "seats_enrolled": h.get("seatsEnrolled"),
        "jobs_completed": (c.get("jobStates") or {}).get("completed"),
        "launches_live": c.get("launchesLive"),
        "sites": c.get("sites"),
        "shipped": pubs.get("all"),
        "audits": pubs.get("audits"),
        "answers_total": (orc.get("byStatus") or {}).get("attested"),
        "ai_tokens": c.get("inferenceTokens"),
        "accepted_total": sum(int(r.get("accepted") or 0) for r in rows) if rows else None,
    }
    # Sanity: more agents online than seats signed up means a bad reading.
    if m["agents_online"] and m["seats_enrolled"] and m["agents_online"] > m["seats_enrolled"]:
        m["agents_online"] = None
    return {k: int(v) for k, v in m.items() if isinstance(v, (int, float))}


def serious_audits(seen):
    """Newly published audits with critical or high findings: [(key, text, source, title)]."""
    data = get_json(f"{API}/publications?type=audits&sort=newest&pageSize=20")
    out = []
    for it in data.get("items") or []:
        for a in it.get("audits") or []:
            key = f"audit:{a.get('jobId')}"
            if key in seen:
                continue
            sev = a.get("severities") or {}
            crit, high = int(sev.get("critical") or 0), int(sev.get("high") or 0)
            seen.append(key)
            if not (crit or high):
                continue
            parts = []
            if crit:
                parts.append(f"{crit} critical")
            if high:
                parts.append(f"{high} high-severity")
            issues = " and ".join(parts) + (" issue" if crit + high == 1 else " issues")
            text = (f"{PREFIX}the IMD swarm's AI audit panel just flagged {issues} in a smart-contract review. "
                    "This is what the review step is for: catching problems early.")
            out.append((key, text, f"{API}/jobs/{a.get('jobId')}", clean(it.get("title"))[:200]))
    del seen[:-500]  # keep the list from growing forever
    return out


def x_ready():
    return all(os.environ.get(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))


def post_single(text, why, st):
    """Post one breaking-news post (or show it on Telegram when X isn't set up yet)."""
    problems = check([text])
    if problems:
        telegram(f"⚠️ Breaking post blocked ({why}): " + "; ".join(problems) + f"\n\n{text}")
        return None
    if not x_ready():
        telegram(f"🧪 Would post ({why}), X keys not set yet:\n\n{text}")
        return "dry"
    tid = tweet(text)
    url = f"https://x.com/{os.environ.get('X_HANDLE', 'i')}/status/{tid}"
    st.setdefault("log", []).append({"at": int(time.time()), "why": why, "url": url})
    del st["log"][:-200]
    telegram(f"🐦 Posted ({why}): {url}")
    return url


def process_replies(st):
    """Act on ✅ / ❌ / 'post: …' replies to drafts sent earlier."""
    pending = load_json(PENDING, {})
    chat, approver = str(os.environ.get("TG_CHAT") or ""), os.environ.get("TG_APPROVER")
    offset = st.get("tg_offset", 0)
    for u in telegram_updates(offset):
        offset = max(offset, u["update_id"] + 1)
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != chat:
            continue
        if approver and str((m.get("from") or {}).get("id")) != str(approver):
            continue
        key = str((m.get("reply_to_message") or {}).get("message_id"))
        item = pending.get(key)
        text = (m.get("text") or "").strip()
        if not item or not text:
            continue
        low = text.lower()
        if low.startswith(("❌", "no", "skip", "drop")):
            pending.pop(key)
            telegram("Dropped.", reply_to=m.get("message_id"))
            continue
        custom = text[5:].strip() if low.startswith("post:") else None
        if not (custom or low.startswith(("✅", "👍", "yes", "ok", "post"))):
            continue
        try:
            if item["kind"] == "weekly":
                mine = [p.strip() for p in re.split(r"\n\s*---\s*\n", custom)] if custom else None
                if mine and check(mine):
                    telegram("Not posted: " + "; ".join(check(mine)) + ". Fix it and reply again.",
                             reply_to=m.get("message_id"))
                    continue
                post_issue(item["no"], posts=mine)
            else:
                st["day"]["count"] += 1
                post_single(custom or item["text"], item.get("why", "approved"), st)
                st.setdefault("done", []).append(item.get("key"))
            pending.pop(key)
        except Exception as e:
            telegram(f"⚠️ Couldn't post: {e}", reply_to=m.get("message_id"))
    # Forget drafts nobody answered within three days.
    cutoff = time.time() - 3 * 86400
    pending = {k: v for k, v in pending.items() if v.get("createdAt", 0) > cutoff}
    st["tg_offset"] = offset
    save_json(PENDING, pending)


def cmd_watch(args):
    st = load_json(WATCH, None)
    now = int(time.time())
    try:
        snap = snapshot()
    except NotReady as e:
        print(f"Skipping this check: {e}")
        return 0
    first = st is None
    if first:
        st = {"startedAt": now, "records": {}, "milestones": {}, "audits_seen": [], "tg_offset": 0}
    st.setdefault("day", {"date": kst_date(), "count": 0})
    if st["day"]["date"] != kst_date():
        st["day"] = {"date": kst_date(), "count": 0}

    process_replies(st)

    posts = []  # (why, text, on_success) to post automatically, in priority order
    warm = now - st["startedAt"] >= WARMUP_DAYS * 86400

    for name, label in MILESTONES.items():
        v = snap.get(name)
        if v is None:
            continue
        mark = floor_nice(v)
        if mark < 100:
            continue                                 # too small to be news
        last = st["milestones"].get(name)
        if last is None or first:
            st["milestones"][name] = mark          # baseline: never post on the first sighting
        elif mark > last:
            # Saved only once posted, so a milestone held back by the daily limit goes out later.
            done = lambda name=name, mark=mark: st["milestones"].__setitem__(name, mark)
            posts.append((f"milestone {name} {mark}", f"{PREFIX}the IMD swarm just passed {fmt(mark)} {label}.", done))

    for name, (template, (kind, step)) in RECORDS.items():
        v = snap.get(name)
        if v is None:
            continue
        r = st["records"].setdefault(name, {"best": v, "posted": v, "postedAt": 0})
        if v <= r["best"]:
            continue
        jump = v > 3 * max(r["best"], 1)       # a sudden 3× jump is more likely a glitch than news
        enough = v >= (r["posted"] + step if kind == "abs" else r["posted"] * (1 + step))
        cooled = now - r.get("postedAt", 0) >= RECORD_COOLDOWN
        r["best"] = v
        if warm and enough and cooled and not jump:
            done = lambda r=r, v=v: r.update(posted=v, postedAt=now)
            posts.append((f"record {name} {v}", PREFIX + template.format(n=fmt(v)) + RECORD_TAIL, done))

    # Serious audit findings always go to Telegram for a yes/no first.
    try:
        found = serious_audits(st["audits_seen"])
    except Exception as e:
        found = []
        print(f"Audit check failed: {e}")
    if first:
        found = []                                   # baseline only
    pending = load_json(PENDING, {})
    for key, text, source, title in found:
        msg = telegram(f"📝 Breaking draft, needs your OK:\n\n{text}\n\nSource: {source}\nReviewed: {title}\n\n"
                       "Reply ✅ to post, ❌ to drop, or reply post: followed by your own wording.")
        if msg:
            pending[str(msg)] = {"kind": "single", "key": key, "text": text, "why": key, "createdAt": now}
    save_json(PENDING, pending)

    for why, text, done in posts:
        if st["day"]["count"] >= MAX_AUTO_PER_DAY:
            print(f"Daily limit reached; will try again later: {text}")
            continue
        try:
            if post_single(text, why, st):
                st["day"]["count"] += 1
                done()
            time.sleep(2)
        except Exception as e:
            print(f"Posting failed: {e}", file=sys.stderr)
            if st.get("last_error") != str(e):
                telegram(f"⚠️ Breaking post failed ({why}): {e}")
            st["last_error"] = str(e)

    st["last"] = {"at": now, **snap}
    save_json(WATCH, st)
    if first:
        telegram("👀 CHANGELOG.md breaking-news watcher is on. Milestones post from now; "
                 f"records start after {WARMUP_DAYS} days of watching. "
                 f"Now: {fmt(snap.get('agents_online', 0))} agents online, "
                 f"{fmt(snap.get('accepted_24h', 0))} pieces of work accepted in 24h.")
    print(json.dumps({"candidates": [w for w, _, _ in posts], "snapshot": snap}, indent=2))
    return 0


# ---------------------------------------------------------------- commands

def cmd_show(args):
    issue, job = load_issue(args.job or (TEST_JOB_ID if args.test else None))
    posts = compose(issue)
    print(f"Issue No. {issue['no']} ({issue.get('week')}) from job {job}\n")
    for i, p in enumerate(posts, 1):
        print(f"--- {i}/{len(posts)} ({weighted_len(p)} chars)\n{p}\n")
    problems = check(posts)
    print("Checks: " + ("passed" if not problems else "; ".join(problems)))
    return 0 if not problems else 1


def cmd_draft(args):
    try:
        issue, job = load_issue(args.job or (TEST_JOB_ID if args.test else None))
    except NotReady as e:
        print(f"Not ready yet: {e}")
        if args.notify_not_ready:
            telegram(f"CHANGELOG.md social: no draft yet. {e}.")
        return 0
    no = issue["no"]
    path = DRAFTS / f"{no:03d}.json"
    if path.exists() and not args.force:
        print(f"Draft for No. {no} already exists; nothing to do.")
        return 0
    posts = compose(issue)
    problems = check(posts)
    DRAFTS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"no": no, "week": issue.get("week"), "job": job, "posts": posts,
                                "problems": problems, "createdAt": int(time.time())}, indent=2, ensure_ascii=False) + "\n")
    preview = "\n\n".join(f"[{i}/{len(posts)}] {p}" for i, p in enumerate(posts, 1))
    head = f"📝 CHANGELOG.md No. {no}: X thread draft ({len(posts)} posts)\n\n"
    if problems:
        tail = "\n\n⚠️ Checks failed, so this can't be posted as is: " + "; ".join(problems)
    else:
        tail = ("\n\n✅ Checks passed. Reply ✅ to this message to post it (it goes out at the next check, "
                "within 30 minutes), ❌ to skip, or reply post: followed by your own thread, with a line "
                f"containing just --- between posts. Or run Post on GitHub: {repo_link('/actions/workflows/post.yml')}")
    msg = telegram(head + preview + tail)
    if msg and not problems:
        pending = load_json(PENDING, {})
        pending[str(msg)] = {"kind": "weekly", "no": no, "createdAt": int(time.time())}
        save_json(PENDING, pending)
    print(f"Saved {path}")
    print(preview)
    return 0


def cmd_post(args):
    no = args.issue or max((int(p.stem) for p in DRAFTS.glob("[0-9][0-9][0-9].json")), default=None)
    if not no:
        print("No drafts to post.")
        return 1
    return post_issue(no, dry_run=args.dry_run, force=args.force)


def post_issue(no, dry_run=False, force=False, posts=None):
    state = load_posted()
    prev = state.get(str(no))
    if prev and prev.get("complete", True) and not force:
        print(f"Issue {no} was already posted: {prev['url']}")
        return 0
    path = DRAFTS / f"{no:03d}.json"
    draft = json.loads(path.read_text())
    if posts:  # your own wording replaces the drafted thread
        draft["posts"], draft["edited"] = posts, True
        path.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n")
    posts = draft["posts"]
    problems = check(posts)
    if problems:
        print("Refusing to post: " + "; ".join(problems))
        telegram(f"⚠️ CHANGELOG.md No. {no} was not posted: " + "; ".join(problems))
        return 1
    if dry_run:
        print("Dry run, nothing posted:\n\n" + "\n\n".join(posts))
        if x_ready():
            try:
                who = verify_x()
                print(f"\nX keys work: they post as @{who}")
                telegram(f"✅ X keys work: they post as @{who}. (Dry run, nothing posted.)")
            except Exception as e:
                print(f"\nX key check failed: {e}")
                telegram(f"⚠️ X key check failed: {e}")
                return 1
        return 0
    me = os.environ.get("X_HANDLE", "i")
    # Resume a thread that stopped part-way instead of starting it again.
    ids = list(prev["ids"]) if prev and not prev.get("complete", True) and not force else []
    if ids:
        print(f"Resuming No. {no} after post {len(ids)} of {len(posts)}")
    for p in posts[len(ids):]:
        ids.append(tweet(p, ids[-1] if ids else None))
        state[str(no)] = {"ids": ids, "url": f"https://x.com/{me}/status/{ids[0]}",
                          "complete": len(ids) == len(posts), "postedAt": int(time.time())}
        save_posted(state)
        time.sleep(2)
    url = state[str(no)]["url"]
    print(f"Posted No. {no}: {url}")
    telegram(f"🐦 CHANGELOG.md No. {no} is on X: {url}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("show", "draft"):
        s = sub.add_parser(name)
        s.add_argument("--job", help="read this IMD job instead of the schedule's newest")
        s.add_argument("--test", action="store_true", help="use the test issue (No. 1)")
        if name == "draft":
            s.add_argument("--force", action="store_true", help="redo an existing draft")
            s.add_argument("--notify-not-ready", action="store_true", help="tell Telegram if no issue is ready")
    p = sub.add_parser("post")
    p.add_argument("--issue", type=int, help="issue number (default: newest draft)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="post even if already posted")
    sub.add_parser("watch")
    args = ap.parse_args()
    try:
        return {"show": cmd_show, "draft": cmd_draft, "post": cmd_post, "watch": cmd_watch}[args.cmd](args)
    except Exception as e:  # report clearly, including to Telegram for scheduled runs
        print(f"Error: {e}", file=sys.stderr)
        if args.cmd != "show":
            telegram(f"⚠️ CHANGELOG.md social {args.cmd} failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

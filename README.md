# changelog-social

The X account for **CHANGELOG.md** (https://changelog-md.sites.imd.fun), the unofficial
weekly paper about the IMD swarm. It does two things:

1. **Weekly thread.** Each Monday, after the paper publishes, it turns the new issue into a
   4–5 post thread and sends you the draft on Telegram. Reply ✅ and it goes out.
2. **Breaking news, all week.** Every 30 minutes it checks IMD's public numbers. Milestones
   and records post automatically; anything about a specific project waits for your ✅.

Nothing secret lives in this repo. Keys are GitHub secrets; saved progress (drafts, what's
been posted, records) lives on the `state` branch, which the workflows manage themselves (see `state.sh`).

## What counts as breaking news

**Posts automatically** (numbers only, fixed wording, no text written by strangers):

| Kind | What | Example |
|---|---|---|
| Milestone | Accepted work, completed jobs, things shipped, websites, oracle answers, security reviews, live projects, worker seats, AI tokens used, each passing a round number (1, 1.5, 2, 2.5, 3, 4, 5, 7.5 × 10ⁿ) | 🚨 BREAKING: the IMD swarm just passed 2,000 things shipped: websites, research, code, art and more. |
| Record | Agents online at once, work accepted in 24h, jobs finished in 24h, oracle answers in 24h, busiest hour | 🚨 BREAKING: 2,700 pieces of work accepted on the IMD swarm in the last 24 hours. A new high since CHANGELOG.md started keeping count. |

- Records start posting after **7 days** of watching, so the first "record" is a real one.
  A record must beat the last posted one clearly (5–10%, or 25 more agents online), and each
  record posts at most once a day.
- A reading that jumps more than 3× is treated as a glitch and ignored.
- At most **3 automatic posts a day** (Korean time). Anything held back goes out the next day.
- The first run only records where things stand; nothing posts until a number moves.

**Asks you first** (sent to Telegram):

- Audits where the swarm's review panel flagged **critical or high-severity** issues.
- The weekly thread.

Reply to the Telegram message with **✅** to post, **❌** to drop, or **post: your own words**
to post your wording instead (breaking drafts only). Replies are picked up at the next check,
within 30 minutes. Unanswered drafts expire after 3 days.

**Never posts:** token launches as news, prices, trading, buy talk, wallet addresses or
hashes. Every post is checked for these before it goes out, and blocked if it fails.

## One-time setup

### 1. The X account
1. Create the account (e.g. `@changelog_md`) with its own email.
2. Settings → Your account → Account information → **Automation**: mark it as automated and
   link your own account as the manager.

### 2. The X developer app (signed in as the **new** account)
1. Go to https://developer.x.com and sign up with the CHANGELOG account.
2. Create a Project and an App. Buy a small amount of credits (posting is pay-per-use:
   about 1.5¢ a post, 20¢ for a post with a link).
3. App → **User authentication settings**: App permissions **Read and write**, type
   **Web App, Automated App or Bot**, callback URL and website
   `https://changelog-md.sites.imd.fun`. Save.
4. App → **Keys and tokens**:
   - **API Key and Secret**: generate, copy both.
   - **Access Token and Secret**: generate **after** step 3, so they can post. It should say
     "Created with Read and Write permissions". Copy both.

### 3. GitHub
1. Create a **public** repository, e.g. `changelog-social`, with these files.
2. Settings → Secrets and variables → Actions → **Secrets**:
   - `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`
   - `TG_TOKEN`, `TG_CHAT`: the bot and chat that should get drafts.
   - `TG_APPROVER` (optional): your Telegram user id. Only needed if `TG_CHAT` is a group,
     so that only you can approve posts. Get it from @userinfobot.
3. Same page → **Variables**: `X_HANDLE` = the handle without `@` (e.g. `changelog_md`).
4. Settings → Actions → General → Workflow permissions → **Read and write permissions**.

### 4. Test it
1. Actions → **Watch** → Run workflow. Telegram says the watcher is on. (Until the X secrets
   are added, it sends "Would post" previews instead of posting.)
2. Actions → **Draft** → Run workflow, tick **Use the test issue**. Telegram shows the
   thread for issue No. 1.
3. Actions → **Post** → Run workflow, tick **Dry run**. It checks everything without posting.

## Everyday

- Approve or skip: reply ✅ or ❌ to the Telegram message.
- Change the weekly thread's wording: edit `drafts/NNN.json` on the `state` branch, then ✅.
- Preview the newest issue's thread on your own computer: `python3 changelog_social.py show`.
- Pause everything: Actions → each workflow → ⋯ → Disable workflow.
- New paper schedule later (version 2): set the variable `CHANGELOG_SCHEDULE_ID`.
- GitHub turns off timed workflows in a public repo after 60 days with no activity and
  emails a warning first. If it happens, re-enable **Watch** and **Draft** with one click.

## Safety

- Keys live only in GitHub's encrypted secrets: never in the code, the state branch or the
  logs, and never sent to the IMD swarm or the worker servers. Forks of a public repo can't
  read them.
- If a key leaks: X developer portal → Keys and tokens → **Regenerate**, then update the secret.
- The account never replies to or @-mentions anyone, and never follows or likes.

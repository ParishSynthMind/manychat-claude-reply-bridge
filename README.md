# ManyChat AI Reply Bridge

[![tests](https://github.com/ParishSynthMind/manychat-claude-reply-bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/ParishSynthMind/manychat-claude-reply-bridge/actions/workflows/tests.yml)

A small, secure HTTPS endpoint that lets a **ManyChat** flow (Instagram DM, Facebook Messenger, WhatsApp, Telegram) get its reply from **Claude**. ManyChat calls it from a **Dynamic Block** or an **External Request**; the bridge answers in ManyChat's own format, inside ManyChat's 10-second limit. For each message it:

- has Claude answer **only from your knowledge base**, with the source shown under the answer,
- **qualifies the lead**: the name, need, budget and timeline the customer actually gave go into ManyChat custom fields, and a `lead-qualified` tag is added once need, budget and timeline are known,
- **hands the chat to a person** (tag `human-handover`, field `ai_paused = true`, an alert to the team) when Claude is unsure, when the customer asks for a person, or when risk words appear (refund, legal, complaint, payment, personal data), and then stays quiet until a person switches it back on,
- says it is an **AI assistant** on the first reply to each contact and on handover,
- handles **STOP / START** opt-out,
- refuses **prompt injection** (customer text is data, never instructions),
- returns the same answer for a repeated request (**idempotency**), so a retry or a replayed request never costs a second AI call.

**Status, 17 November 2026:** built and tested offline against a faithful copy of ManyChat's documented behaviour (209 tests, CI green), and **deployed live on Render** with real Claude Haiku 5.5 calls, exercised over the internet with exactly the requests ManyChat sends. **ManyChat itself has NOT called this endpoint yet**: Dynamic Block and External Request are only available on ManyChat's paid plans, and the test account is on the free plan. Details: [What was proven live, and what was not](#what-was-proven-live-and-what-was-not).

## How it works

```
Customer DMs on Instagram / Messenger / WhatsApp / Telegram
        |
ManyChat flow: Default Reply -> (ai_paused is not true) -> Dynamic Block  POST /manychat/dynamic
        |                                                   or External Request POST /manychat/external
        |   headers: X-Bridge-Secret      body: {"channel": "...", "contact": {{Full Contact Data}}}
        v
Bridge (FastAPI)
  1. secret or HMAC signature check, 32 KB body cap, per-IP rate limit
  2. idempotency: same contact + message + interaction time -> stored answer, no AI call
  3. kill switch, opt-out / opt-in, paused (a person has the chat), per-contact limits
  4. code rules first: risk words, "talk to a person", prompt-injection patterns -> handover, no AI call
  5. daily AI cap (spend limit in code)
  6. Claude (structured JSON output) with the matching knowledge-base articles and the last few turns
     - hard deadline: answer or fall back before 8 s (ManyChat stops waiting at 10 s, fixed)
  7. code checks Claude's answer: citations must be articles it was given, confidence >= 0.7,
     no links, no secrets, length limit; otherwise -> handover
        |
        v
ManyChat v2 dynamic content: messages (+ "Book a free call" button), actions (tags, custom fields),
quick replies where the channel supports them   -- or flat JSON for External Request mapping
```

ManyChat's rules are enforced in the reply, so it is never rejected for shape or size: at most **10 messages, 5 actions and 11 quick replies**, quick replies only on Instagram and Messenger (ManyChat does not support them on WhatsApp or Telegram), no `content.type` for Messenger, long answers split under each channel's text limit, and actions ordered so the important ones (handover, opt-out, lead tag) fit in the 5. Anything that does not fit can be applied through ManyChat's Public API (below).

**Example reply (real, from the live run):** see [`examples/live-run/run.jsonl`](examples/live-run/run.jsonl).

### The slow path (optional, paid ManyChat plans)
If Claude is slower than the deadline, the customer gets a short holding message and the flow continues. With `MANYCHAT_API_KEY` set, the holding message is "Give me a few seconds, I'm checking that for you." and the real answer is sent a moment later through ManyChat's Public API (`/fb/sending/sendContent`, plus `addTagByName` / `setCustomFieldByName` for the actions). This path is tested offline only.

## What was proven live, and what was not

| Part | Status |
|---|---|
| The bridge running on a public HTTPS host (Render free plan) | **Proven live** 17 Nov 2026 |
| Real Claude (claude-haiku-5-5) answering from the knowledge base with citations | **Proven live** |
| ManyChat-format requests (Full Contact Data body, secret header, 10-second timeout, Fallback on error) sent over the internet, every reply checked against ManyChat's documented v2 rules | **Proven live** with this repo's ManyChat stand-in (`mc_bridge/fake_manychat.py`) as the caller |
| Lead fields, handover, pause, opt-out/opt-in, prompt-injection refusal, idempotent replay, wrong-secret 401 | **Proven live** in the same run |
| ManyChat itself calling the endpoint from a real flow | **Not run.** Dynamic Block and External Request need a paid ManyChat plan ("available only on paid plans and isn't accessible on accounts with a free subscription", ManyChat Help Center, Dev Tools: Basics); the test account is on the free plan and no paid plan or trial was started |
| A real Instagram / Messenger / WhatsApp / Telegram contact receiving the reply | **Not run** (needs the paid plan and a connected channel) |
| Slow path and extra actions through ManyChat's Public API | **Mocked only** (faithful fake of the Page API from ManyChat's Swagger: Bearer key, 3011 error outside 24 h) |
| HMAC-signed mode (for n8n, Make or a Cloudflare Worker in front) | **Tested offline** |

Everything about ManyChat's behaviour that the stand-in copies comes from ManyChat's own documentation, read on 17 November 2026: Help Center articles *Dev Tools: Basics*, *Dev Tools: Dynamic block*, *Dev Tools: External request*, *Response Reference for Instagram, WhatsApp, and Telegram Automation*, the Dynamic Block reference on manychat.github.io, and the Page API Swagger at api.manychat.com/swagger.

## Run it

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock -r requirements-dev.txt && pip install -e . --no-deps
pytest -q                       # 209 tests, all offline: ManyChat and Claude are faked
cp .env.example .env            # fill in BRIDGE_SECRETS and ANTHROPIC_API_KEY
mc-bridge new-secret            # makes a value for BRIDGE_SECRETS
mc-bridge ask "How much is the Transform program?"   # one question through the real Claude
mc-bridge serve                 # local server on :8000 (ManyChat needs HTTPS: deploy it, below)
```

### Deploy (Render free plan)
`render.yaml` is a Render Blueprint: in Render choose **New > Blueprint**, pick this repo, and enter `BRIDGE_SECRETS` and `ANTHROPIC_API_KEY` when asked (stored encrypted by Render, never in the repo). Then open `https://<service>.onrender.com/health`. Any host that runs Python behind HTTPS works the same way.

### Connect ManyChat
Step by step, with both flow versions (Dynamic Block and External Request) and the field mapping: **[docs/MANYCHAT-SETUP.md](docs/MANYCHAT-SETUP.md)**. ManyChat has no flow import format, so the flow is written out there.

## Settings that matter (`config/bridge.yaml`)
- `model`: one line to change the Claude model.
- `deadline_seconds`: 8.0 by default; must stay under ManyChat's fixed 10 seconds.
- `min_confidence`, `show_sources`, `answer_buttons`, `quick_replies`, the tag and field names, the customer-facing messages.
- `risk_words`, `human_words`, `opt_out_words`: the code rules that hand over or opt out before any AI call.
- `max_ai_calls_per_day`, `per_contact_per_minute`, `per_contact_per_day`, `per_ip_per_minute`: spend and abuse limits.
- `kill_switch` (or `KILL_SWITCH=true`): no AI calls at all; every message is tagged for a person.
- `require_signature`, `max_interaction_age_seconds`: stricter replay protection (below).
- Environment only: `BRIDGE_SECRETS`, `ANTHROPIC_API_KEY`, `MANYCHAT_API_KEY`, `ALERT_WEBHOOK_URL`, `HEARTBEAT_URL`, `AI_DISCLOSURE`, `AI_DISCLOSURE_REMOVED_BY_WRITTEN_REQUEST`.

## Security
- **Who can call it.** ManyChat does not sign its requests, so you add your own header: `X-Bridge-Secret`, a long random value compared in constant time. Two values can be active at once, so the secret can be rotated without downtime. The server refuses to start without one.
- **Signed mode.** If something that can sign sits in front (n8n, Make, a Cloudflare Worker), set `require_signature: true`: HMAC-SHA256 over `timestamp.body`, a 5-minute window and a one-use signature cache (a replayed request gets 409).
- **Replays in secret mode.** The same contact + message + ManyChat interaction time returns the stored answer, never a second AI call. `max_interaction_age_seconds` can also reject requests whose ManyChat `last_interaction` is old; it is off by default because it has not been checked against a live ManyChat account.
- **Customer text is data.** It is cleaned, capped at 1,000 characters and wrapped before Claude sees it; known injection patterns go straight to a person; Claude's answer must cite articles it was actually given and may not contain links or anything that looks like a key.
- **No personal data in logs.** Logs are JSON with a fixed list of keys: a hashed contact reference, channel, action, reason category, latency, token counts. Never message text, names, emails, phone numbers or raw ids (there is a test for this).
- **Errors** never echo the request back, and API docs pages are switched off.
- **Spend:** a daily AI-call cap in code, on top of a monthly limit on the client's own Anthropic workspace.

## Monitoring (failure, empty-run, heartbeat)
- `ALERT_WEBHOOK_URL` (Slack, Teams, n8n...) gets failures (AI errors, slow-path errors, unhandled errors), handovers (reason category only) and the empty-run alert.
- `HEARTBEAT_URL` (Healthchecks.io, UptimeRobot) gets a ping after successful answers, at most once a minute: when the pings stop, the dead-man's switch raises the alarm.
- `mc-bridge daily-check`, run once a day, raises the empty-run alert when no message reached the bridge that day.
- ManyChat's own **Settings > Logs** shows Dynamic Block errors, and the flow's **Fallback** step always gives the customer an answer.

## Versions pinned
| Part | Version | Notes |
|---|---|---|
| Anthropic Python SDK | 2.9.0 (`anthropic>=2.9,<3`) | sends API version 2026-10-21; no `temperature`/`top_p` sent |
| Claude model | `claude-haiku-5-5` | one setting in `config/bridge.yaml` |
| ManyChat Dynamic Block format | `v2` | "Dynamic Block API's current version is v2" (Help Center, 29 Oct 2026) |
| ManyChat Public API | Page API (`/fb/...`), Bearer key | slow path only |
| Python | 3.12 | CI on ubuntu-24.04; actions pinned by commit |
| Everything else | `requirements.lock` | full pip freeze; `pip-audit`: 0 known vulnerabilities before and after (17 Nov 2026) |

## Limits
- ManyChat's Dynamic Block / External Request need a **paid ManyChat plan**; ManyChat has not called this endpoint in testing (see the table above).
- **Free hosting:** Render's free plan sleeps after 15 idle minutes and takes up to a minute to wake, longer than ManyChat's 10 seconds, so the first message after a quiet spell would get the Fallback. A real deployment uses an always-on plan. The free plan also has no disk, so opt-out, pause and disclosure flags reset when the service restarts (ManyChat's own `opted-out` tag and `ai_paused` field are still respected, because they arrive with every request).
- The knowledge base is a small Markdown file with keyword search, fine for a few hundred articles; larger catalogues need a proper search index.
- English demo content (a made-up coaching business). Claude replies in the customer's language, but only English was tested live.
- No conversation history comes from ManyChat; the bridge keeps the last 6 turns per contact for 24 hours.
- The `ai_paused` hand-back relies on a person setting the field to No in ManyChat's Live Chat; ManyChat's own "pause automation" control is not used.

## Support runbook (Tier 1)
| Symptom | Check | Fix |
|---|---|---|
| Customers get the Fallback message | ManyChat Settings > Logs; `GET /health` | 401: secret in ManyChat header differs from `BRIDGE_SECRETS`. Timeout: host asleep or slow; use an always-on plan. 5xx: read the host logs, alert payload names the error |
| Bot answers nobody, no errors | contact field `ai_paused`, tag `opted-out`, `kill_switch` | Set `ai_paused` to No; remove `opted-out` only if the customer asked (START) |
| Tags or fields not written | ManyChat Settings > Tags / Fields | Create the missing tag or field with the exact name in `config/bridge.yaml` (ManyChat ignores unknown ones) |
| Everything goes to a person | alert reasons: `daily AI cap reached`, `ai error: AuthenticationError` | Raise the cap, or fix/rotate the Anthropic key; check the workspace spend limit |
| Model retired | Anthropic deprecation notice | Change `model` in `config/bridge.yaml`, run the tests, redeploy |

## Project layout
```
mc_bridge/server.py        FastAPI app: /manychat/dynamic, /manychat/external, /health
mc_bridge/bridge.py        the decision order, deadline, idempotency, handover, slow path
mc_bridge/ai.py            Claude call (structured output) and the code checks on its answer
mc_bridge/formatter.py     ManyChat v2 dynamic content and External Request JSON, with ManyChat's limits
mc_bridge/security.py      shared secret (rotation) and HMAC-signed mode
mc_bridge/inbound.py       Full Contact Data -> one clean message
mc_bridge/guard.py, kb.py  risk/opt-out/injection rules; knowledge base search
mc_bridge/store.py         SQLite: flags, short history, idempotency, nonces, counters
mc_bridge/manychat_api.py  Public API client (slow path)
mc_bridge/fake_manychat.py ManyChat stand-in used by the tests and the live run
scripts/live_run.py        the live run against a deployed bridge
docs/MANYCHAT-SETUP.md     building the flow in ManyChat
```

MIT licence. The demo business and every test contact are made up.

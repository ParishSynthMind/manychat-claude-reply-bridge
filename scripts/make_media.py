"""Render the portfolio images from the real live-run record (examples/live-run/run.jsonl).

The chat pictures are drawings of the real replies the deployed bridge sent in the
live run; they are not ManyChat or Instagram screenshots (ManyChat did not call the
bridge, see the README). Needs Playwright with Chromium.
"""
import html
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
RUN = [json.loads(line) for line in (ROOT / "examples" / "live-run" / "run.jsonl").open()]
STEPS = [r for r in RUN if isinstance(r.get("step"), int)]
SUMMARY = (ROOT / "examples" / "live-run" / "summary.txt").read_text().splitlines()
DATE = "17 Nov 2026"
CSS = """
* { box-sizing: border-box; margin: 0; }
body { font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; color: #111; }
.bg { background: linear-gradient(135deg, #1b1035 0%, #3a1c71 45%, #d76d77 85%, #ffaf7b 100%); }
.phone { background: #fff; border-radius: 36px; box-shadow: 0 30px 60px rgba(0,0,0,.35); overflow: hidden; }
.bar { padding: 18px 22px; border-bottom: 1px solid #eee; font-weight: 700; display: flex; gap: 12px; align-items: center; }
.av { width: 38px; height: 38px; border-radius: 50%; background: linear-gradient(45deg,#f58529,#dd2a7b,#8134af); }
.msgs { padding: 18px; display: flex; flex-direction: column; gap: 10px; }
.me { align-self: flex-end; background: #3797f0; color: #fff; border-radius: 20px 20px 4px 20px; padding: 11px 15px; max-width: 78%; }
.bot { align-self: flex-start; background: #efefef; border-radius: 20px 20px 20px 4px; padding: 11px 15px; max-width: 84%; white-space: pre-wrap; }
.btn { align-self: flex-start; border: 1px solid #3797f0; color: #3797f0; border-radius: 12px; padding: 7px 14px; font-weight: 600; font-size: .9em; }
.note { font-size: 13px; color: #666; }
"""


def esc(t):
    return html.escape(t)


def bubbles(steps, size=15):
    out = []
    for r in steps:
        out.append(f'<div class="me">{esc(r["customer"])}</div>')
        for i, d in enumerate(r["delivered"]):
            out.append(f'<div class="bot">{esc(d)}</div>')
        msgs = (r.get("response") or {}).get("content", {}).get("messages", [])
        if msgs and msgs[-1].get("buttons"):
            out.append(f'<div class="btn">{esc(msgs[-1]["buttons"][0]["caption"])}</div>')
    return f'<div class="msgs" style="font-size:{size}px">' + "".join(out) + "</div>"


def page(body, w, h):
    return f"<html><head><style>{CSS}</style></head><body style='width:{w}px;height:{h}px'>{body}</body></html>"


def thumbnail():
    a = [s for s in STEPS if s["contact"] == "test-7001001"][2:3]
    body = f"""<div class="bg" style="width:1200px;height:900px;display:flex;align-items:center;padding:70px;gap:60px">
      <div style="flex:1;color:#fff">
        <div style="font-size:30px;opacity:.85;font-weight:600">ManyChat &nbsp;+&nbsp; Claude</div>
        <div style="font-size:68px;font-weight:800;line-height:1.05;margin:18px 0 26px">AI replies for<br>Instagram DMs</div>
        <div style="font-size:27px;line-height:1.5;opacity:.95">Answers from your knowledge base<br>Lead name, goal, budget into custom fields<br>Refunds and complaints to a person<br>Inside ManyChat's 10-second limit</div>
        <div style="margin-top:34px;font-size:20px;opacity:.8">Deployed and tested live, {DATE}</div>
      </div>
      <div class="phone" style="width:470px">
        <div class="bar"><div class="av"></div>peakform.coaching</div>
        {bubbles(a, 17)}
        <div style="padding:0 18px 18px"><span style="background:#e7f6ec;color:#147a3a;border-radius:8px;padding:5px 10px;font-size:14px;font-weight:700">lead-qualified</span>
        <span style="background:#eef;color:#336;border-radius:8px;padding:5px 10px;font-size:14px;font-weight:700;margin-left:6px">lead_budget = About 200 USD</span></div>
      </div></div>"""
    return page(body, 1200, 900)


def dm():
    a = [s for s in STEPS if s["contact"] == "test-7001001"]
    body = f"""<div class="bg" style="width:1200px;height:1500px;padding:50px 60px">
      <div style="color:#fff;font-size:34px;font-weight:800">Live run, {DATE}: one Instagram conversation</div>
      <div style="color:#fff;opacity:.85;font-size:18px;margin:8px 0 26px">Real replies from the deployed bridge and real Claude Haiku 5.5, drawn as a DM thread from examples/live-run/run.jsonl. Not a ManyChat screenshot.</div>
      <div class="phone" style="width:1080px">
        <div class="bar"><div class="av"></div>peakform.coaching (made-up demo business)</div>
        {bubbles(a, 19)}
      </div></div>"""
    return page(body, 1200, 1500)


def actions():
    rows = []
    for r in STEPS:
        tags = ", ".join(r["contact_tags_after"]) or "-"
        fields = "<br>".join(f"{esc(k)} = {esc(str(v))[:46]}" for k, v in r["contact_fields_after"].items()
                             if k != "ai_reply") + ("<br>ai_reply = (the answer text)" if "ai_reply" in r["contact_fields_after"] else "") or "-"
        if r["mode"] == "external":
            first = "(External Request) ai_reply = " + r["response"]["reply"].split("\n\n")[1][:50]
        else:
            first = (r["delivered"][-1] if r["delivered"] else "(no message: bot stays quiet)")[:80]
        rows.append(f"<tr><td>{r['step']}</td><td>{r['channel']}</td><td>{esc(r['customer'][:58])}</td>"
                    f"<td>{esc(first)}</td><td>{esc(tags)}</td><td>{fields}</td><td>{r['seconds']} s</td></tr>")
    body = f"""<div style="width:1600px;padding:40px;background:#fff">
      <div style="font-size:32px;font-weight:800">What the bridge wrote back to ManyChat: live run, {DATE}</div>
      <div class="note" style="font-size:17px;margin:8px 0 18px">{esc(' | '.join(SUMMARY[1:6]))}</div>
      <style>table{{border-collapse:collapse;width:100%;font-size:15px}}td,th{{border-bottom:1px solid #ddd;padding:9px 8px;vertical-align:top;text-align:left}}th{{background:#f4f0ff}}</style>
      <table><tr><th>#</th><th>Channel</th><th>Customer wrote</th><th>Last reply</th><th>Contact tags after</th><th>Custom fields after</th><th>Time</th></tr>{''.join(rows)}</table>
      <div class="note" style="margin-top:14px">Requests sent in ManyChat's exact format (Full Contact Data body, secret header, 10 s timeout) by the repo's ManyChat stand-in; every reply passed ManyChat's documented v2 rules. Test contacts are made up.</div></div>"""
    return page(body, 1600, 1200)


def flow():
    box = "background:#fff;border-radius:16px;padding:18px 22px;box-shadow:0 8px 20px rgba(0,0,0,.18);font-size:20px"
    arrow = "<div style='color:#fff;font-size:34px;text-align:center'>&#8595;</div>"
    body = f"""<div class="bg" style="width:1200px;height:900px;padding:50px 120px">
      <div style="color:#fff;font-size:36px;font-weight:800;margin-bottom:22px">How a DM gets its answer</div>
      <div style="{box}"><b>Customer</b> sends a DM (Instagram, Messenger, WhatsApp, Telegram)</div>{arrow}
      <div style="{box}"><b>ManyChat flow</b>: Default Reply, skip if <code>ai_paused</code>, then a Dynamic Block: POST with a secret header and the contact's data</div>{arrow}
      <div style="{box}"><b>Bridge</b>: secret check, duplicate check, opt-out, rate limits, risk words and injection go straight to a person</div>{arrow}
      <div style="{box}"><b>Claude</b> answers from the knowledge base with sources and picks out name, goal, budget, timeline; code checks the answer</div>{arrow}
      <div style="{box}"><b>ManyChat</b> sends the reply and button, adds tags, fills custom fields. Under 8 s, or the fallback message.</div>
    </div>"""
    return page(body, 1200, 900)


def main():
    media = ROOT / "media"
    media.mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, (htm, w, h, full) in {"thumbnail-4x3.png": (thumbnail(), 1200, 900, False),
                                        "live-run-dm.png": (dm(), 1200, 1500, True),
                                        "live-run-actions.png": (actions(), 1600, 1200, True),
                                        "flow-diagram.png": (flow(), 1200, 900, False)}.items():
            pg = b.new_page(viewport={"width": w, "height": h})
            pg.set_content(htm)
            if full:
                pg.screenshot(path=str(media / name), full_page=True)
            else:
                pg.screenshot(path=str(media / name))
            pg.close()
            print("wrote", name)
        b.close()


if __name__ == "__main__":
    main()

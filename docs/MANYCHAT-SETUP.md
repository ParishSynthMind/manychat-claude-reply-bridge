# Setting it up in ManyChat (about 15 minutes)

ManyChat's Dynamic Block and External Request are **paid-plan features** ("available only on paid plans and isn't accessible on accounts with a free subscription", ManyChat Help Center, Dev Tools: Basics, updated 27 Oct 2026). ManyChat has no flow import file format, so the flow is described step by step here.

## 1. Before you start
1. Deploy the bridge (see the README: Render free plan or any HTTPS host) and open `https://<your-host>/health`: it should show `"ok": true`.
2. Make a shared secret: `mc-bridge new-secret`. Put it in the host's `BRIDGE_SECRETS` setting. You will paste the same value into ManyChat below.
3. In ManyChat, **Settings > Tags**: create `ai-answered`, `human-handover`, `opted-out`, `lead-qualified`, `ai-timeout`. ManyChat only applies tags that already exist.
4. **Settings > Fields** (custom user fields): create Text fields `lead_name`, `lead_need`, `lead_budget`, `lead_timeline`, and a True/False field `ai_paused`. For the External Request version also create Text fields `ai_reply` and `ai_sources`.

## 2. The flow: Dynamic Block version (recommended)
```
Trigger: Default Reply (Instagram DM / Messenger / WhatsApp / Telegram)
  -> Condition: ai_paused is not true            (a person has the chat: stop here)
  -> Send Message node with ONE Dynamic Block:
        Method:  POST
        URL:     https://<your-host>/manychat/dynamic
        Headers: X-Bridge-Secret: <your secret>
                 Content-Type: application/json
        Body:    {"channel": "instagram", "contact": {{Full Contact Data}}}
        Fallback: Text "Thanks for your message! Our team will get back to you here shortly."
                  + Action: add tag human-handover
```
- Use `"channel": "telegram"`, `"whatsapp"` or `"messenger"` in the flow for that channel (or add `?channel=telegram` to the URL).
- The bridge's reply already contains the messages, buttons and actions (tags and custom fields), so nothing else is needed after the block.
- Always set the Fallback: ManyChat stops the whole automation when a Dynamic Block fails and has no fallback.

## 3. The flow: External Request version (when you want ManyChat to send the message itself)
```
Trigger: Default Reply
  -> Condition: ai_paused is not true
  -> Action: Make External Request
        Method:  POST
        URL:     https://<your-host>/manychat/external
        Headers: X-Bridge-Secret: <your secret>
        Body:    {"channel": "instagram", "contact": {{Full Contact Data}}}
        Response mapping (JSON path -> field):
            $.reply         -> ai_reply
            $.sources       -> ai_sources
            $.handover      -> ai_paused
            $.lead_name     -> lead_name
            $.lead_need     -> lead_need
            $.lead_budget   -> lead_budget
            $.lead_timeline -> lead_timeline
  -> Condition: ai_reply is not empty
        yes -> Send Message: {{ai_reply}}
  -> Condition: ai_paused is true -> notify the team (Notify Admin action) and add tag human-handover
```
ManyChat only maps values when the request is triggered by a real contact or a flow preview, not on the "Test Request" button, and never when the status is not 200 (Dev Tools: Basics).

## 4. Handing the chat back to the bot
When a person has finished, set the contact's `ai_paused` field to **No** in the Live Chat sidebar. The bridge sees the field on the next message and answers again.

## 5. Checks before going live
- Send a test DM from a test account: the first reply starts with the AI disclosure line.
- Send "refund": the reply says a person will reply, the contact gets `human-handover` and `ai_paused = Yes`.
- Send "STOP": the contact gets `opted-out` and the bot goes quiet; "START" turns it back on.
- Look at **Settings > Logs** in ManyChat for any Dynamic Block errors.

## 6. Optional: the slow path (paid plans with API access)
ManyChat waits at most 10 seconds and the limit cannot be changed. The bridge answers or falls back before 8 seconds. If you also set `MANYCHAT_API_KEY` (ManyChat **Settings > API**), a reply that takes longer is not lost: the customer first gets "Give me a few seconds, I'm checking that for you." and the bridge sends the real answer a moment later through ManyChat's Public API (`/fb/sending/sendContent`), plus any tags or fields that did not fit in ManyChat's 5-action limit. ManyChat only allows this inside the 24-hour messaging window (error 3011 otherwise), which is always the case for a reply to a message just received.

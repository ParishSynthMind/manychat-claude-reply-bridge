# Account needs (what only the account owner can do)

Status 17 Nov 2026. Nothing here blocks the portfolio piece; these are optional upgrades.

1. **ManyChat calling the endpoint for real (optional, costs money):** Dynamic Block and External Request are paid-plan features in ManyChat ("available only on paid plans and isn't accessible on accounts with a free subscription", Help Center, Dev Tools: Basics). The test account is on the free plan and still on the "Select Channel" screen. To run it: start the Pro 14-day trial (needs a card, cancel before day 14), connect a test channel (Telegram is quickest: a @BotFather bot token in Settings > Telegram), create the tags and fields in docs/MANYCHAT-SETUP.md, build the flow, put the secret from Keychain item `manychat-bridge-secret` in the header, and DM the test bot. Not done: no spend without Parish's OK.
2. **Slow path live (optional):** needs ManyChat Settings > API (paid plans) and `MANYCHAT_API_KEY` set on Render.
3. **Render demo:** service `manychat-claude-reply-bridge` (free plan, Frankfurt) uses Neo's Anthropic key with a 300-calls-a-day cap in code and the shared secret; it sleeps when idle. Delete it in the Render dashboard if the demo is no longer wanted.

# Deploying the hosted version (Free / Pro / Premium / Ultimate)

Two modes, set in `.env`:

| | `AUTH_MODE=off` (default) | `AUTH_MODE=on` |
|---|---|---|
| Who | You, on your own computer | Your customers, on your server |
| Accounts | None, everything unlocked (`LOCAL_TIER=ultimate`) | Sign-up / sign-in, per-user prop accounts, alerts and paper trading |
| Plans | n/a | Free, Pro, Premium, Ultimate (edit `backend/app/accounts/tiers.py`) |

## 1. Server (about $10–40/month)
Any small Linux VPS works (Hetzner, DigitalOcean, Vultr, Lightsail): 2 vCPU / 4 GB RAM is plenty to start.
Install Docker, then:

```bash
git clone <your repo> desk && cd desk
cp .env.example .env            # then edit it (below)
DOMAIN=desk.yourdomain.com docker compose up -d --build
```
Point the domain's DNS **A record** at the server first; Caddy then gets the HTTPS certificate automatically.
Users, workspaces and the data cache live in the `terminal-data` volume, so redeploys keep them.

Platforms like Fly.io, Render or Railway also work with the `Dockerfile`: mount a persistent volume at `/data`
and keep **one** instance (the market-data feed and websocket stream run in-process).

## 2. `.env` for production
```ini
AUTH_MODE=on
DATA_MODE=live
PUBLIC_URL=https://desk.yourdomain.com
ADMIN_EMAILS=you@yourdomain.com
COOKIE_SECURE=on
EIA_API_KEY=...                 # see docs/DATA_SOURCES.md
# Stripe (step 3)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_PREMIUM=price_...
STRIPE_PRICE_ULTIMATE=price_...
```

## 3. Payments with Stripe
1. Create a Stripe account → **Products** → add *Pro*, *Premium*, *Ultimate*, each with a monthly recurring price
   (optionally an annual price too). Copy each `price_...` id into `.env`.
2. **Developers → Webhooks → Add endpoint**: `https://desk.yourdomain.com/api/billing/webhook`, events
   `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`. Copy the signing
   secret (`whsec_...`).
3. **Settings → Billing → Customer portal**: turn it on (lets customers upgrade, downgrade and cancel themselves).
4. Test with `sk_test_...` keys and Stripe's test card `4242 4242 4242 4242` before switching to live keys.

The webhook is the source of truth: a user's tier changes only when Stripe confirms. Stripe handles cards and
PCI compliance; you never see card numbers. If you'd rather not deal with sales tax / VAT registration yourself,
a merchant-of-record service (Paddle, Lemon Squeezy) is the alternative; the tier logic stays the same.

## 4. Admin
```bash
docker compose exec terminal python -m app.tools.users list
docker compose exec terminal python -m app.tools.users set-tier tester@example.com premium   # comp a beta tester
```
Back up the `terminal-data` volume (it holds `app.db` and user workspaces), e.g. a nightly `docker run --rm -v ...` tar to object storage.

## 5. Before taking money (checklist)
- **Data licences.** Yahoo prices are fine for your own use but not for resale. For paying customers use a
  licensed feed (Databento/dxFeed/Barchart redistribution licence) or "bring your own data" (customers connect their
  own broker/prop API). EIA, CFTC and NOAA data are U.S. public domain. Open-Meteo needs its commercial plan.
- **Regulation.** Personalised futures trading advice can require CTA registration (CFTC/NFA). Keep the product a
  research and risk tool where the user sets their own risk, and get a futures regulatory lawyer to review the
  copilot and signals before launch.
- **Terms, privacy policy, risk disclaimer** on the sign-up page.
- **Hosted-mode limits today:** trade-signal notifications (Discord/Telegram/webhook) go to the server owner's
  channels, not per user; live order routing is disabled in hosted mode until per-user broker linking exists.

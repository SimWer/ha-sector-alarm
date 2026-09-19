# Sector Alarm — Home Assistant integration

A clean, fresh `custom_component` for [Sector Alarm](https://www.sectoralarm.no/)
(Norway / Sweden / Finland / Denmark) that talks to the My Pages REST API at
`mypagesapi.sectoralarm.net`.

It exposes:

| Entity type           | What you get                               |
|-----------------------|--------------------------------------------|
| `alarm_control_panel` | Arm Away / Arm Home / Disarm               |
| `binary_sensor`       | Door & window contact sensors              |
| `sensor`              | Temperature **and** humidity sensors       |
| `lock`                | Smart locks (lock / unlock)                |

## Installation

### Via HACS (recommended)

1. In HACS → **Integrations** → top-right menu → **Custom repositories**.
2. Add this repo URL with category **Integration**.
3. Install **Sector Alarm**, then restart Home Assistant.

### Manual

Copy `custom_components/sector_alarm/` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

1. **Settings → Devices & services → Add integration → Sector Alarm**.
2. Enter:
   - **Email** & **Password** for My Pages
   - **Panel ID** — the 8-digit number in the URL after logging in to
     `mypages.sectoralarm.net` (e.g. `#!/systems/01234567`)
   - **PIN code** — required to arm/disarm and lock/unlock; leave blank for read-only
3. Done. Devices auto-populate after the first refresh.

If your password changes, Home Assistant will prompt for re-authentication automatically.

## Options

After setup, click **Configure** on the integration card to change the polling
interval (default 300 s, min 120, max 3600).

## Polling and Sector's rate limit

Sector's API allows **60 requests per clock hour**, account-wide, resetting on
the hour. This is measured, not documented: a 429 from their edge carries
`x-envoy-ratelimited: true` and nothing else — no `Retry-After`, no quota
headers, empty body — so there is no way to discover the limit at runtime.

The integration therefore polls in tiers rather than fetching everything every
time. Only the alarm state changes minute to minute:

| Data | Calls | Refreshed |
|------|-------|-----------|
| Alarm state (`GetPanelStatus`) | 1 | every poll |
| Temperatures, humidity, doors/windows | 3 | every 20 min |
| Inventory, names, locks (`GetPanel`) | 1 | hourly |

At the 300 s default that is **~22 requests/hour**, with alarm state never more
than five minutes stale. Shortening the interval raises only the first row, so
120 s costs 40/hour and still fits; the integration logs a warning at startup if
your configuration cannot.

Two consequences worth knowing:

- **Restarting Home Assistant costs requests** (a login plus a poll). A burst of
  restarts while debugging can eat a noticeable share of the hour's budget.
- If you do exceed the limit, every endpoint returns 429 until the next hour
  boundary. Entities go unavailable and the config entry sits in `setup_retry`
  until the quota resets.

## Caveats

- Sector Alarm's API is undocumented and may change without notice. If anything
  stops responding, open an issue with the redacted **Diagnostics** download from
  the integration card.
- Only one panel per config entry. Add multiple entries if you have more sites.
- Push/realtime updates aren't available — Sector doesn't expose a websocket, so
  this is polling only, and the rate limit above caps how often that can happen.
- Auth is via Sector's Auth0-backed flow (`isNewSASCustomer` accounts). Legacy
  cookie-only customers from before the migration aren't tested.
- Smoke detectors and water-leak detectors aren't wired up yet (Sector exposes
  them on separate endpoints) — patches welcome.

## License

MIT

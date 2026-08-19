# terminal.thequantphilosopher.com

Static Hyperliquid funding terminal. Rebuilt **hourly by GitHub Actions**
(`.github/workflows/publish.yml`) — no server required.

- `publish_terminal.py` — fetches every perp dex (main + builder dexes),
  90d hourly funding history per market, trailing-average APRs and market caps
  (Yahoo for stock perps, CoinGecko for crypto), then renders `index.html`.
- `terminal_template.html` + `lw.js` — page template and TradingView
  lightweight-charts (inlined at render time).
- `asset_map.json` / `asset_map_new.json` — ticker → category / real-world identity.
- `data/hist/*.json` — per-market hourly funding, fetched on demand by the page.

Run locally: `python publish_terminal.py . --render-only`

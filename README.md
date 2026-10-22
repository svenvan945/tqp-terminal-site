# terminal.thequantphilosopher.com

Static Hyperliquid funding terminal. Rebuilt **hourly by GitHub Actions**
(`.github/workflows/publish.yml`) — no server required.

- `publish_terminal.py` — fetches every perp dex (main + builder dexes),
  92d hourly funding history per market, trailing-average APRs and market caps
  (Yahoo for stock perps, CoinGecko for crypto), then renders `index.html`.
- `terminal_template.html` + `lw.js` — page template and TradingView
  lightweight-charts (inlined at render time).
- `asset_map.json` / `asset_map_new.json` — ticker → category / real-world identity.
- `data/hist/*.json` — per-market hourly funding, fetched on demand by the page.

Run locally: `python publish_terminal.py . --render-only`

## Publishing and recovery

The publisher fetches funding history afresh on every build, so a successful
refresh also backfills hours missed during an outage. The page's **Updated**
timestamp is the market snapshot time, not the deployment completion time.
Existing tabs reload hourly; reload after a new deployment to see it immediately.

GitHub can disable scheduled workflows in a public repository after 60 days
without repository activity. Successful Pages artifact deployments alone did
not prevent this site's `disabled_inactivity` outage on 19 October 2026.

To restore publishing and rebuild both the table and funding histories:

```powershell
gh workflow enable publish.yml --repo svenvan945/tqp-terminal-site
gh workflow run publish.yml --repo svenvan945/tqp-terminal-site --ref master
gh run list --repo svenvan945/tqp-terminal-site --workflow publish.yml --limit 5
```

Allow build and deploy to finish (the last pre-outage build took about 20
minutes). Verify that the page's **Updated** timestamp advances and that
`data/hist/BTC.json` and other history files contain recent settled hours. Also
verify a later scheduled run; a successful manual run alone does not prove the
schedule is operating.

Re-enabling once does not permanently remove the inactivity condition. Monitor
public snapshot freshness independently of this workflow and review scheduling
if the source repository will remain unchanged for long periods.

See [GitHub's scheduled-workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

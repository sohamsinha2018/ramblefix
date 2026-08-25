# RambleFix download counter (Cloudflare Worker)

Counts real downloads, streams the DMGs from private R2, and shows the public number only after it passes 100.

## Deploy (Soham)
1. `cd download-counter`
2. `wrangler kv namespace create COUNTS`  -> copy the id into wrangler.toml
3. `wrangler deploy`  -> gives you a URL like https://ramblefix-dl.<your-subdomain>.workers.dev

## Wire the site (tell the site agent)
- Point the download buttons at the Worker, never R2 directly, so downloads stay counted and avoid fragile public R2 hostnames:
  - English:        https://ramblefix-dl.<sub>.workers.dev/dl/lite
  - English + Hindi: https://ramblefix-dl.<sub>.workers.dev/dl/hi
- Show the count: fetch https://ramblefix-dl.<sub>.workers.dev/count
  - if `visible` is true, render e.g. "12,431 downloads"
  - if `visible` is false (under 100), render nothing.

## Smoke test
Run before and after every site/Worker/download change:

```bash
npm run smoke:downloads
```

The test checks:
- the live site links to the Worker, not `r2.dev`
- both DMGs return clean `HEAD` headers
- both DMGs support a 1 KB range read through the Worker
- filenames, content type, sizes, and count endpoint shape are correct

Monitoring probes send `x-ramblefix-download-probe: smoke`, so they do not inflate download counts.

GitHub Actions runs `.github/workflows/download-smoke.yml` hourly, after site deploys, and on download/site changes.

## Notes
- Threshold is 100 (change THRESHOLD in worker.js).
- KV is eventually consistent, so the count can lag/undercount slightly under heavy concurrency. Fine for a display counter. If you ever need exact, switch to D1 or a Durable Object.

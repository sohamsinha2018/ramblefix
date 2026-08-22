# RambleFix Site

Static launch site for RambleFix.

Live fallback URL:

```text
https://sohamsinha2018.github.io/ramblefix/
```

Primary launch domain: `https://ramblefix.app/`.

The static site deploys to Vercel first. Keep the GitHub Pages URL as a fallback until
GoDaddy DNS points `ramblefix.app` at the Vercel project and HTTPS is active.

Before publishing, configure the links in `index.html`:

- Download — English: direct GitHub Release DMG asset URL.
- Download — English + Hindi: direct GitHub Release DMG asset URL.
- Star on GitHub: public repo URL.
- GitHub Discussions: repo discussions URL.
- Join Discord: optional invite URL. If omitted, the secondary feedback button points to GitHub Discussions.

Command:

```bash
RAMBLEFIX_LITE_DOWNLOAD_URL="https://pub-98d9f6faa545400b9f2dd67be1585b33.r2.dev/RambleFix-Lite-0.1.0.dmg" \
RAMBLEFIX_HI_DOWNLOAD_URL="https://pub-98d9f6faa545400b9f2dd67be1585b33.r2.dev/RambleFix-HI-0.1.0.dmg" \
RAMBLEFIX_GITHUB_URL="https://github.com/<owner>/<repo>" \
RAMBLEFIX_DISCUSSIONS_URL="https://github.com/<owner>/<repo>/discussions" \
script/configure_site_links.sh
```

Do not link the bare `https://github.com/sohamsinha2018/ramblefix/releases` page from the public site. Download CTAs should point to stapled `.dmg` assets. Current public copy should say `Apple Silicon Mac (M1+), macOS 13+`; the underlying release requirement is Apple silicon/arm64, with no Intel build tested or published yet.

The page has no signup or third-party browser runtime dependency. It sends only explicit,
anonymous product events through the first-party `/api/track` endpoint. There is no
autocapture, cookie, person profile, or session replay. Configure Vercel with:

```text
POSTHOG_PROJECT_TOKEN=<project token>
POSTHOG_HOST=https://us.i.posthog.com
```

The tracked events are `site viewed`, `site cta clicked`, `github star clicked`,
`download requested`, `language vote clicked`, and `demo switched`. Download links must use
`data-analytics-event="download requested"` and a stable `data-analytics-target` value.

Local smoke:

```bash
script/run_final_launch_eval.sh --allow-placeholders
```

Public launch gate:

```bash
script/run_final_launch_eval.sh --public
```

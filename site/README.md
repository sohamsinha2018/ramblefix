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

- Download — English: direct stapled DMG asset URL.
- Download — English + Hindi: direct stapled DMG asset URL.
- View source code: public repo URL.
- Language route issues: GitHub issues labelled `language route`.

Command:

```bash
RAMBLEFIX_LITE_DOWNLOAD_URL="https://pub-98d9f6faa545400b9f2dd67be1585b33.r2.dev/RambleFix-Lite-0.1.0.dmg" \
RAMBLEFIX_HI_DOWNLOAD_URL="https://pub-98d9f6faa545400b9f2dd67be1585b33.r2.dev/RambleFix-HI-0.1.0.dmg" \
RAMBLEFIX_GITHUB_URL="https://github.com/<owner>/<repo>" \
RAMBLEFIX_DISCUSSIONS_URL="https://github.com/<owner>/<repo>/discussions" \
script/configure_site_links.sh
```

Do not link the bare `https://github.com/sohamsinha2018/ramblefix/releases` page from the public site. Download CTAs should point to stapled `.dmg` assets. Current first-fold public copy should say `Modern Mac, macOS 13+`; the exact install disclosure should say the V0 builds support Apple silicon Macs (M1 or newer), with no Intel build tested or published yet.

GitHub release notes should still exist for public credibility, but the v0.1.0 DMGs are larger than GitHub's per-asset limit. Keep the actual public install links on R2 unless the artifacts are reduced below 2 GiB or replaced with a smaller bootstrap installer.

Contribution copy should not say "open a PR" without a reason. The agenda is: pick a language issue, add corpus/model/eval evidence, then PR a route or fix only if it improves the same benchmark without regressing English.

Privacy copy must use this framing: "Your voice and transcribed text never leave your Mac. Optional anonymous usage stats (counts & timings only) are on to help improve the app — turn them off anytime."

The page has no signup or third-party browser runtime dependency. It sends only explicit,
anonymous product events through the first-party `/api/track` endpoint. There is no
autocapture, cookie, person profile, or session replay. Configure Vercel with:

```text
POSTHOG_PROJECT_TOKEN=<project token>
POSTHOG_HOST=https://us.i.posthog.com
```

The tracked events are `site viewed`, `site cta clicked`, `download requested`,
`language vote clicked`, `demo switched`, `builderr clicked`, `builderr challenge clicked`,
and `builder profile clicked`. Download links must use
`data-analytics-event="download requested"` and a stable `data-analytics-target` value.

Local smoke:

```bash
script/run_final_launch_eval.sh --allow-placeholders
```

Public launch gate:

```bash
script/run_final_launch_eval.sh --public
```

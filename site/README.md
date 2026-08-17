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

- Download for Mac: GitHub Releases DMG URL.
- Star on GitHub: public repo URL.
- GitHub Discussions: repo discussions URL.
- Join Discord: optional invite URL. If omitted, the secondary feedback button points to GitHub Discussions.

Command:

```bash
RAMBLEFIX_DOWNLOAD_URL="https://github.com/<owner>/<repo>/releases/download/v0.1.0/RambleFix-0.1.0.dmg" \
RAMBLEFIX_GITHUB_URL="https://github.com/<owner>/<repo>" \
RAMBLEFIX_DISCUSSIONS_URL="https://github.com/<owner>/<repo>/discussions" \
script/configure_site_links.sh
```

The page has no analytics, signup, or third-party runtime dependency.

Local smoke:

```bash
script/run_final_launch_eval.sh --allow-placeholders
```

Public launch gate:

```bash
script/run_final_launch_eval.sh --public
```

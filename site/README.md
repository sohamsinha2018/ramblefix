# DictaHue Site

Static launch site for `dictahue.app`.

Before publishing, configure the links in `index.html`:

- Download for Mac: GitHub Releases DMG URL.
- Star on GitHub: public repo URL.
- GitHub Discussions: repo discussions URL.
- Join Discord: optional invite URL. If omitted, the secondary feedback button points to GitHub Discussions.

Command:

```bash
DICTAHUE_DOWNLOAD_URL="https://github.com/<owner>/<repo>/releases/download/v0.1.0/DictaHue-0.1.0.dmg" \
DICTAHUE_GITHUB_URL="https://github.com/<owner>/<repo>" \
DICTAHUE_DISCUSSIONS_URL="https://github.com/<owner>/<repo>/discussions" \
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

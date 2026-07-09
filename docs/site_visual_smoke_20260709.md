# DictaHue Site Visual Smoke - 2026-07-09

## Result

Passed.

Command:

```bash
script/smoke_site_visual.sh
```

What it checks:

- local static server starts and shuts down,
- `/` and `/styles.css` return successfully,
- required launch copy is present,
- expanded real-use proof copy is present,
- desktop, tablet, and mobile screenshots are captured.

## Screenshots

| Viewport | Artifact | Size |
| --- | --- | --- |
| Desktop | `output/playwright/dictahue-site-desktop.png` | 1440 x 1000 |
| Tablet | `output/playwright/dictahue-site-tablet.png` | 1024 x 768 |
| Mobile | `output/playwright/dictahue-site-mobile.png` | 390 x 844 |
| Full page | `output/playwright/dictahue-site-fullpage.png` | 1440 x full page |

## Visual Read

- Desktop and tablet hero render cleanly.
- Mobile hero keeps the core pitch and CTAs visible, with the product mockup starting below the fold.
- The benchmark section now leads with the expanded real-use proof: `0.872 useful` overall, `0.906 useful` Hindi+English under 60s, and `0 unsafe` structure updates.
- Benchmark caveat is present: long English is not a launch claim.
- Download/GitHub/feedback links are still placeholders until public repo/release/community URLs exist.

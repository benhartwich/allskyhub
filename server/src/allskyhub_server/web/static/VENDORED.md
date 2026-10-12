# Vendored files

| File | Source | Version | License |
|---|---|---|---|
| `htmx.min.js` | npm `htmx.org` (`dist/htmx.min.js`), sha256 checked against the registry tarball | 2.0.11 | 0BSD |
| `uplot.min.js`, `uplot.min.css` | npm `uplot` (`dist/uPlot.iife.min.js`, `dist/uPlot.min.css`), sha512 integrity checked against the registry | 1.6.32 | MIT |
| `../devices/data/constellations.json` | npm `d3-celestial` 0.7.35 (`data/constellations.lines.json`, `data/constellations.json`), sha512 integrity checked; reduced by `tools/sky/build_constellations.py` (lines, German names) | 0.7.35 | BSD-3-Clause |
| `packages/design/.../static/fonts/space-grotesk.woff2` (served as `/static/design/fonts/…`) | npm `@fontsource-variable/space-grotesk` (`files/space-grotesk-latin-wght-normal.woff2`), headings only | 5.3.0 | OFL-1.1 |

No build step and no CDN: the CSP allows only `'self'`.

# Vendored files

| File | Source | Version | License |
|---|---|---|---|
| `htmx.min.js` | npm `htmx.org` (`dist/htmx.min.js`), sha256 checked against the registry tarball | 2.0.11 | 0BSD |
| `uplot.min.js`, `uplot.min.css` | npm `uplot` (`dist/uPlot.iife.min.js`, `dist/uPlot.min.css`), sha512 integrity checked against the registry | 1.6.32 | MIT |
| `three-0.186.1/three.module.js`, `three-0.186.1/three.core.js` (the folder carries the version, so they are cached for good) | npm `three` (`build/three.module.js`, `build/three.core.js`, unchanged), sha512 integrity checked against the registry; only on the Satellites 3D page | 0.186.1 | MIT |
| `globe/earth-day.jpg`, `globe/earth-night.jpg` | NASA Visible Earth „Blue Marble: Land Surface, Shallow Water, and Shaded Topography“ (https://commons.wikimedia.org/wiki/File:Land_shallow_topo_2048.jpg, originally https://visibleearth.nasa.gov/images/57752) and „Earth at Night“ (https://commons.wikimedia.org/wiki/File:The_earth_at_night.jpg), public domain; the 3840×1920 renderings of Commons, scaled to 2048×1024 (JPEG q78) on 2026-11-15 | – | public domain |
| `packages/sky/src/allskyhub_sky/data/constellations.json` | npm `d3-celestial` 0.7.35 (`data/constellations.lines.json`, `data/constellations.json`), sha512 integrity checked; reduced by `tools/sky/build_constellations.py` (lines, German names) | 0.7.35 | BSD-3-Clause |
| `packages/design/.../static/fonts/space-grotesk.woff2` (served as `/static/design/fonts/…`) | npm `@fontsource-variable/space-grotesk` (`files/space-grotesk-latin-wght-normal.woff2`), headings only | 5.3.0 | OFL-1.1 |

No build step and no CDN: the CSP allows only `'self'`.

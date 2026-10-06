# Research: what makes all-sky cameras hard (October 2026)

## Where users get stuck

Source: 1,387 user-reported problems and questions from GitHub Issues and Discussions,
October 2024 to October 2026: 668 from AllskyTeam/allsky and 719 from
aaronwmorris/indi-allsky. Items written by maintainers are excluded. The items were
classified by keyword rules; a hand check found the main category correct in 75–83 %
of cases.

| Area | allsky | indi-allsky |
|---|---|---|
| Installation, OS and Python dependencies | 12 % | 16 % |
| Camera detection and drivers | 7 % | 13 % |
| Exposure, image quality, darks, mask, focus | 9 % | 12.5 % |
| Timelapse, keogram, startrails not produced | 10 % | 10 % |
| Updates breaking things | 7 % | 4 % |
| Dew heater, fan, sensors | 6 % | 8 % |
| Remote website and upload (FTP/SFTP/S3) | 14 % | 7 % |
| Overlay, modules, external data | 20 % | 10 % |

**Problems in both projects** (installation, drivers, exposure, products, dew, storage)
make up about half of each project's support traffic. They come from all-sky cameras
in general, so a product has to solve them.

**Problems specific to one project:**

- **allsky:** remote website, FTP and upload, which an outbound hub connection removes.
- **indi-allsky:** INDI drivers and its web and database stack.

**Estimated share of support traffic that would disappear:**

| Approach | Share |
|---|---|
| A preinstalled image on curated hardware | about 46 % (range 35–55 %) |
| A complete kit (camera, housing, dew heater) | about 59 % (range 45–70 %) |
| An outbound hub connection on its own | only 7–14 % |

**Limits of these numbers:** people who gave up during setup never post, so setup
problems are probably undercounted.

## Market

**Free software:**

- AllskyTeam/allsky has about 1.6k GitHub stars.
- indi-allsky has about 475.

**Commercial cameras and services:**

- Starlight Xpress Oculus costs about EUR 1,190 and needs a PC; it has no cloud.
- Alcor OMEA and other professional systems cost EUR 3,700 and more.
- SkySphere costs USD 999 and is due to ship in December 2026. It focuses on AI
  detection for security, UAP and research users.
- allskykamera.space is a free hosted network with about 89 cameras.

**Gap:** a complete camera under EUR 500 that pairs with the phone, connects outbound to
a hub, has a mobile app with push alerts and a hosted public sky page.

**Model:** like consumer weather stations (Netatmo, Ecowitt, Davis). The hardware is the
product, the core app is free, and an optional paid tier adds history, alerts,
multi-site and API access.

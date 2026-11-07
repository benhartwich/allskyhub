"""The network's page /netz (SharedSky, SPEC §10.6; devices/network.py): what the cameras
and stations measure together, and who takes part, by name or as "eine Station im Netz"."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request
from fastapi.responses import Response

from allskyhub_server.devices import camera_map, map_tiles, network
from allskyhub_server.web.deps import DbSession, OptionalSession
from allskyhub_server.web.render import render

router = APIRouter()

# The scale of the darkest-sky bars, mag/arcsec²: city to a very dark site.
BAR_LOW, BAR_HIGH = 18.0, 22.0


@router.get("/netz")
async def network_page(request: Request, db: DbSession, session: OptionalSession) -> Response:
    cache: network.OverviewCache = request.app.state.network
    view = await cache.get(db, dt.datetime.now(dt.UTC))
    _, lat, lon, zoom = camera_map.REGIONS["europa"]
    config = {
        "view": [lat, lon, zoom],
        "layer": "",
        "maxZoom": map_tiles.MAX_ZOOM,
        "layers": {
            key: {"maxZoom": t.max_zoom, "credit": t.credit} for key, t in map_tiles.LAYERS.items()
        },
        "cameras": [p.as_json() for p in view.points],
        "reports": [],
    }
    bars = [
        max(2, min(100, round((r.sqm - BAR_LOW) / (BAR_HIGH - BAR_LOW) * 100)))
        for r in view.darkest
    ]
    return render(
        request,
        "network.html",
        {"view": view, "map_config": config, "bars": bars, "unit": camera_map.SQM_UNIT},
        session=session,
    )

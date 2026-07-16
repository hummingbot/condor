from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from condor.web.ws_manager import get_ws_manager

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # Loopback posture (§5.5): reject foreign Origin/Host before the upgrade
    # (DNS rebinding / hostile pages driving the local API) — the sole gate,
    # no per-connection identity.
    from condor.web.security import websocket_origin_allowed

    if not websocket_origin_allowed(ws):
        await ws.close(code=4003, reason="non-loopback origin")
        return
    manager = get_ws_manager()
    conn = await manager.connect(ws)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                await manager.handle_message(conn, raw)
            except WebSocketDisconnect:
                raise
            except Exception:
                # One malformed/failing message must not tear down the whole
                # multiplexed connection (all subscribed channels).
                log.exception("WS message error (ignored)")
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WS error")
    finally:
        manager.disconnect(conn)

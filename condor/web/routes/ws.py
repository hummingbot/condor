from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from condor.web.ws_manager import get_ws_manager

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)):
    manager = get_ws_manager()
    conn = await manager.connect(ws, token)
    if conn is None:
        return

    try:
        while True:
            raw = await ws.receive_text()
            await manager.handle_message(conn, raw)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.disconnect(conn)

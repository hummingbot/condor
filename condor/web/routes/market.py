from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from config_manager import get_config_manager
from condor.web.auth import get_current_user
from condor.web.models import CandleData, MarketPriceResponse, WebUser

router = APIRouter(tags=["market"])


@router.get("/servers/{name}/market/connectors")
async def get_connectors(name: str, user: WebUser = Depends(get_current_user)):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(name, ServerDataType.CANDLE_CONNECTORS)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@router.get("/servers/{name}/market/prices", response_model=MarketPriceResponse)
async def get_price(
    name: str,
    connector: str = Query(...),
    trading_pair: str = Query(...),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        result = await get_server_data_service().get_or_fetch(
            name, ServerDataType.PRICES, connector_name=connector, trading_pair=trading_pair
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if result is None:
        raise HTTPException(status_code=502, detail="Failed to fetch price")

    if isinstance(result, (int, float)):
        return MarketPriceResponse(connector=connector, trading_pair=trading_pair, mid_price=float(result))
    elif isinstance(result, dict):
        return MarketPriceResponse(
            connector=connector,
            trading_pair=trading_pair,
            mid_price=float(result.get("mid_price", result.get("price", 0))),
            best_bid=float(result.get("best_bid", 0)),
            best_ask=float(result.get("best_ask", 0)),
        )
    raise HTTPException(status_code=502, detail="Unexpected response format")


@router.get("/servers/{name}/market/candles", response_model=list[CandleData])
async def get_candles(
    name: str,
    connector: str = Query(...),
    trading_pair: str = Query(...),
    interval: str = Query(default="1m"),
    limit: int = Query(default=100, ge=1, le=1000),
    user: WebUser = Depends(get_current_user),
):
    cm = get_config_manager()
    if not cm.has_server_access(user.id, name):
        raise HTTPException(status_code=403, detail="No access")

    client = await cm.get_client(name)
    try:
        result = await client.market_data.get_candles(connector, trading_pair, interval, limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    candles_raw = result if isinstance(result, list) else result.get("data", []) if isinstance(result, dict) else []

    candles = []
    for c in candles_raw:
        if isinstance(c, dict):
            candles.append(
                CandleData(
                    timestamp=float(c.get("timestamp", 0)),
                    open=float(c.get("open", 0)),
                    high=float(c.get("high", 0)),
                    low=float(c.get("low", 0)),
                    close=float(c.get("close", 0)),
                    volume=float(c.get("volume", 0)),
                )
            )
        elif isinstance(c, (list, tuple)) and len(c) >= 6:
            candles.append(
                CandleData(
                    timestamp=float(c[0]),
                    open=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    volume=float(c[5]),
                )
            )
    return candles

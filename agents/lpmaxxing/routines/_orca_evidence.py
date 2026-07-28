import math
import re
from datetime import datetime
from typing import Any

REDACTED_KEYS = {
    "authorization",
    "bearertoken",
    "clientsecret",
    "cookie",
    "credentials",
    "idtoken",
    "mnemonic",
    "privatekey",
    "proxyauthorization",
    "refreshtoken",
    "secret",
    "secretkey",
    "seedphrase",
    "setcookie",
    "password",
    "passphrase",
    "apikey",
    "accesstoken",
    "walletaddress",
    "owneraddress",
}

REDACTED_KEY_MARKERS = (
    "accesstoken",
    "apikey",
    "apisecret",
    "authtoken",
    "clientsecret",
    "password",
    "passphrase",
    "privatekey",
    "refreshtoken",
    "secret",
    "secretkey",
)

CREDENTIAL_URL_RE = re.compile(r"(https?://)[^/\s:@]+:[^@\s/]+@", re.IGNORECASE)
AUTHORIZATION_ASSIGNMENT_RE = re.compile(
    r"(?P<key>[\"']?authorization[\"']?)(?P<separator>\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<scheme>bearer|basic|apikey)\s+"
    r"[^\s,;&}\]\"']+(?P=quote)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<key_quote>[\"']?)(?P<key>authorization|api[_-]?key|api[_-]?secret|access[_-]?token|"
    r"refresh[_-]?token|auth[_-]?token|private[_-]?key|client[_-]?secret|"
    r"password|passphrase|secret)(?P=key_quote)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[\"'][^\"']*[\"']|[^\s,;&}]+)",
    re.IGNORECASE,
)


def _sensitive_key(value: Any) -> bool:
    normalized = "".join(
        character for character in str(value).lower() if character.isalnum()
    )
    return normalized in REDACTED_KEYS or any(
        marker in normalized for marker in REDACTED_KEY_MARKERS
    )


def _redact_text(value: str) -> str:
    value = CREDENTIAL_URL_RE.sub(r"\1[redacted]@", value)
    value = AUTHORIZATION_ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group('key')}{match.group('separator')}"
            f"{match.group('quote')}{match.group('scheme')} [redacted]"
            f"{match.group('quote')}"
        ),
        value,
    )

    def replace_assignment(match: re.Match[str]) -> str:
        raw_value = match.group("value")
        quote = raw_value[0] if raw_value[:1] in {'"', "'"} else ""
        return (
            f"{match.group('key_quote')}{match.group('key')}"
            f"{match.group('key_quote')}{match.group('separator')}"
            f"{quote}[redacted]{quote}"
        )

    return SECRET_ASSIGNMENT_RE.sub(replace_assignment, value)


def text(value: Any) -> str:
    return str(value or "").strip()


def number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def redact(value: Any, *, datetime_iso: bool = True) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if _sensitive_key(key)
                else redact(item, datetime_iso=datetime_iso)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item, datetime_iso=datetime_iso) for item in value]
    if isinstance(value, datetime):
        return value.isoformat() if datetime_iso else str(value)
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


def executor_id(executor: dict[str, Any]) -> str | None:
    config = executor.get("config") if isinstance(executor.get("config"), dict) else {}
    return (
        text(executor.get("executor_id") or executor.get("id") or config.get("id"))
        or None
    )


def executor_controller_id(executor: dict[str, Any]) -> str | None:
    config = executor.get("config") if isinstance(executor.get("config"), dict) else {}
    return text(executor.get("controller_id") or config.get("controller_id")) or None


def looks_like_executor(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    config = value.get("config") if isinstance(value.get("config"), dict) else {}
    return bool(
        value.get("executor_id")
        or value.get("id")
        or value.get("status")
        or value.get("trading_pair")
        or config.get("id")
        or config.get("trading_pair")
    )


def executor_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in ("executor", "data", "result", "item"):
        nested = value.get(key)
        if isinstance(nested, dict) and looks_like_executor(nested):
            return nested
    return value if looks_like_executor(value) else {}


def executor_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [
            item
            for item in value
            if isinstance(item, dict) and looks_like_executor(item)
        ]
    if isinstance(value, dict):
        for key in ("executors", "data", "results", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [
                    item
                    for item in nested
                    if isinstance(item, dict) and looks_like_executor(item)
                ]
        executor = executor_dict(value)
        return [executor] if executor else []
    return []


def recognized_executor_list(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if isinstance(value, list):
        executors = executor_list(value)
        return executors, len(executors) == len(value)
    if isinstance(value, dict):
        for key in ("executors", "data", "results", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                executors = executor_list(value)
                return executors, len(executors) == len(nested)
    return [], False


def executor_rows(value: Any) -> list[dict[str, Any]]:
    rows = value
    if isinstance(value, dict):
        rows = next(
            (
                value[key]
                for key in ("executors", "data", "results", "items")
                if isinstance(value.get(key), list)
            ),
            None,
        )
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("executor search response is not a recognized list")
    return rows


def _next_cursor(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    cursor = response.get("next_cursor") or response.get("cursor")
    pagination = response.get("pagination")
    if not cursor and isinstance(pagination, dict):
        cursor = pagination.get("next_cursor") or pagination.get("cursor")
    return str(cursor) if cursor else None


async def search_controller_executors(
    client: Any,
    controller_id: str,
    *,
    capture_pages: bool = False,
    strict: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[Any]]:
    rows: list[dict[str, Any]] = []
    pages: list[Any] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(200):
        params: dict[str, Any] = {"controller_ids": [controller_id], "limit": 100}
        if cursor:
            params["cursor"] = cursor
        response = await client.executors.search_executors(**params)
        if capture_pages:
            pages.append(redact(response))
        if strict:
            page, recognized = recognized_executor_list(response)
            if not recognized:
                raise ValueError("unrecognized executor search response")
        else:
            page = executor_rows(response)
        rows.extend(page)
        next_cursor = _next_cursor(response)
        if not next_cursor:
            return (rows, pages) if capture_pages else rows
        if next_cursor in seen_cursors:
            raise ValueError("executor pagination cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise ValueError("executor pagination exceeded safety limit")


def _nested_value(value: Any, paths: list[str]) -> Any:
    for path in paths:
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            if current is not None:
                return current
    return None


def normalize_gateway_pool_info(
    value: Any, expected_pool_address: str, observed_at: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Gateway pool info must be an object")
    if isinstance(value.get("result"), dict):
        value = value["result"]
    pool_address = text(
        _nested_value(value, ["pool_address", "poolAddress", "address"])
    )
    base_mint = text(
        _nested_value(
            value,
            [
                "base_mint",
                "baseMint",
                "base_token_address",
                "token_a.mint",
                "token_a.address",
                "tokenA.mint",
                "tokenA.address",
                "tokenMintA.mint",
                "tokenMintA.address",
                "tokenMintA",
            ],
        )
    )
    quote_mint = text(
        _nested_value(
            value,
            [
                "quote_mint",
                "quoteMint",
                "quote_token_address",
                "token_b.mint",
                "token_b.address",
                "tokenB.mint",
                "tokenB.address",
                "tokenMintB.mint",
                "tokenMintB.address",
                "tokenMintB",
            ],
        )
    )
    current_price = number(
        _nested_value(value, ["current_price", "currentPrice", "price"])
    )
    if (
        not pool_address
        or pool_address != expected_pool_address
        or not base_mint
        or not quote_mint
        or current_price is None
    ):
        raise ValueError("Gateway pool info is missing identity or current price")
    return {
        "pool_address": pool_address,
        "base_mint": base_mint,
        "quote_mint": quote_mint,
        "current_price": current_price,
        "observed_at": observed_at,
    }


def _token_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows = value.get("tokens")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _token_address(token: dict[str, Any]) -> str:
    return text(token.get("address") or token.get("token_address") or token.get("mint"))


def _candidate_identity(candidate: dict[str, Any]) -> dict[str, str]:
    token_a = (
        candidate.get("token_a") if isinstance(candidate.get("token_a"), dict) else {}
    )
    token_b = (
        candidate.get("token_b") if isinstance(candidate.get("token_b"), dict) else {}
    )
    return {
        "pool_address": text(candidate.get("pool_address")),
        "token_a_mint": text(token_a.get("mint")),
        "token_b_mint": text(token_b.get("mint")),
    }


async def ensure_gateway_tokens(
    client: Any,
    network_id: str,
    candidate: dict[str, Any],
    gateway_pool_info: dict[str, Any],
) -> list[dict[str, Any]]:
    identity = _candidate_identity(candidate)
    if (
        not identity["pool_address"]
        or identity["pool_address"] != text(gateway_pool_info.get("pool_address"))
        or identity["token_a_mint"] != text(gateway_pool_info.get("base_mint"))
        or identity["token_b_mint"] != text(gateway_pool_info.get("quote_mint"))
    ):
        raise ValueError("candidate and Gateway token identities do not match")
    if not hasattr(client, "gateway"):
        raise RuntimeError("Gateway configuration API is unavailable")
    metadata = []
    for key in ("token_a", "token_b"):
        token = candidate.get(key) if isinstance(candidate.get(key), dict) else {}
        address, symbol, decimals_value = (
            text(token.get("mint")),
            text(token.get("symbol")),
            token.get("decimals"),
        )
        if not address or not symbol or isinstance(decimals_value, bool):
            raise ValueError(f"{key} metadata is incomplete")
        try:
            numeric_decimals = float(decimals_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} decimals are invalid") from exc
        if not numeric_decimals.is_integer() or not 0 <= numeric_decimals <= 18:
            raise ValueError(f"{key} decimals are invalid")
        metadata.append((address, symbol, int(numeric_decimals)))
    rows = _token_rows(await client.gateway.get_network_tokens(network_id))
    missing = []
    for address, symbol, decimals in metadata:
        exact = next((row for row in rows if _token_address(row) == address), None)
        if exact is None:
            collision = next(
                (
                    row
                    for row in rows
                    if text(row.get("symbol")).upper() == symbol.upper()
                    and _token_address(row) != address
                ),
                None,
            )
            if collision is not None:
                raise ValueError(f"Gateway token symbol collision for {symbol}")
            missing.append((address, symbol, decimals))
            continue
        observed_symbol, observed_decimals = text(exact.get("symbol")), exact.get(
            "decimals"
        )
        try:
            observed_decimals = int(observed_decimals)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}") from exc
        if observed_symbol.upper() != symbol.upper() or observed_decimals != decimals:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}")
    for address, symbol, decimals in missing:
        await client.gateway.add_token(
            network_id=network_id,
            address=address,
            symbol=symbol,
            decimals=decimals,
            name=symbol,
        )
    if missing:
        rows = _token_rows(await client.gateway.get_network_tokens(network_id))
    evidence = []
    for address, symbol, decimals in metadata:
        exact = next((row for row in rows if _token_address(row) == address), None)
        if exact is None:
            raise ValueError(f"Gateway token registration did not expose {symbol}")
        observed_symbol, observed_decimals = text(exact.get("symbol")), exact.get(
            "decimals"
        )
        try:
            observed_decimals = int(observed_decimals)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}") from exc
        if observed_symbol.upper() != symbol.upper() or observed_decimals != decimals:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}")
        evidence.append(
            {
                "symbol": symbol,
                "mint": address,
                "decimals": decimals,
                "registered": True,
            }
        )
    return evidence


def normalize_wallet_balances(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("scoped connector balances were not returned")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        available = None
        for key in ("available_units", "available", "available_balance", "units"):
            if key in row:
                available = number(row.get(key))
                if available is not None:
                    break
        symbol = text(row.get("token") or row.get("symbol"))
        if symbol and available is not None:
            normalized.append(
                {
                    "symbol": symbol,
                    "mint": text(
                        row.get("mint")
                        or row.get("token_address")
                        or row.get("address")
                    ),
                    "available": available,
                }
            )
    return normalized


async def fetch_wallet_balances(
    client: Any, account_name: str, connector_name: str
) -> list[dict[str, Any]]:
    balances = await client.portfolio.get_state(
        account_names=[account_name], connector_names=[connector_name], refresh=True
    )
    account = balances.get(account_name)
    if not isinstance(account, dict):
        raise ValueError("scoped account balances were not returned")
    return normalize_wallet_balances(account.get(connector_name))


def balance_for_token(
    balances: list[dict[str, Any]], token: dict[str, Any]
) -> float | None:
    wanted_symbol, wanted_mint = text(token.get("symbol")).upper(), text(
        token.get("mint")
    )
    mint_metadata_present = any(text(balance.get("mint")) for balance in balances)
    symbol_matches = []
    for balance in balances:
        value = (
            balance.get("available")
            if "available" in balance
            else balance.get("available_units")
        )
        available = number(value)
        if available is None:
            continue
        if (
            wanted_mint
            and text(balance.get("mint") or balance.get("address")) == wanted_mint
        ):
            return available
        if text(balance.get("symbol")).upper() == wanted_symbol:
            symbol_matches.append(available)
    if wanted_mint and mint_metadata_present:
        return None
    return symbol_matches[0] if len(symbol_matches) == 1 else None


def balance_for_symbol(
    balances: list[dict[str, Any]], symbol: str, mint: str = ""
) -> float | None:
    return balance_for_token(balances, {"symbol": symbol, "mint": mint})

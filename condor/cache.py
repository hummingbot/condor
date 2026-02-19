"""
Unified TTL cache for Condor.

Stores (key -> (value, timestamp)) tuples inside a dict-like store
(typically context.user_data or a UserDataStore). Each domain uses its
own namespace key to avoid collisions with existing pickle data.

Namespaces in use:
    _cache            - DEX handlers (default)
    _cex_cache        - CEX handlers
    _bots_cache       - Bots handlers
    _executors_cache  - Executors handlers
"""

import functools
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL = 60  # seconds


def get_cached(
    store: dict,
    key: str,
    ttl: int = DEFAULT_CACHE_TTL,
    namespace: str = "_cache",
) -> Optional[Any]:
    """Get a cached value if still valid.

    Args:
        store: Dict-like object (e.g. context.user_data)
        key: Cache key
        ttl: Time-to-live in seconds
        namespace: Dict key holding the cache sub-dict

    Returns:
        Cached value or None if expired/missing
    """
    cache = store.get(namespace, {})
    entry = cache.get(key)

    if entry is None:
        return None

    value, timestamp = entry
    if time.time() - timestamp > ttl:
        return None

    return value


def set_cached(
    store: dict,
    key: str,
    value: Any,
    namespace: str = "_cache",
) -> None:
    """Store a value in the cache.

    Args:
        store: Dict-like object
        key: Cache key
        value: Value to cache
        namespace: Dict key holding the cache sub-dict
    """
    if namespace not in store:
        store[namespace] = {}

    store[namespace][key] = (value, time.time())


def clear_cache(
    store: dict,
    key: Optional[str] = None,
    namespace: str = "_cache",
) -> None:
    """Clear cached values.

    Args:
        store: Dict-like object
        key: Specific key to clear, or None to clear all.
             If key ends with ``*``, clears all keys starting with that prefix.
        namespace: Dict key holding the cache sub-dict
    """
    if key is None:
        store.pop(namespace, None)
    elif namespace in store:
        if key.endswith("*"):
            prefix = key[:-1]
            keys_to_clear = [k for k in store[namespace] if k.startswith(prefix)]
            for k in keys_to_clear:
                store[namespace].pop(k, None)
        else:
            store[namespace].pop(key, None)


async def cached_call(
    store: dict,
    key: str,
    fetch_func: Callable,
    ttl: int = DEFAULT_CACHE_TTL,
    *args,
    namespace: str = "_cache",
    **kwargs,
) -> Any:
    """Execute an async function with caching.

    Args:
        store: Dict-like object
        key: Cache key
        fetch_func: Async function to call on cache miss
        ttl: Time-to-live in seconds
        *args, **kwargs: Passed to fetch_func
        namespace: Dict key holding the cache sub-dict

    Returns:
        Cached or freshly-fetched result
    """
    cached = get_cached(store, key, ttl, namespace=namespace)
    if cached is not None:
        logger.debug(f"Cache hit for '{key}' (ns={namespace})")
        return cached

    logger.debug(f"Cache miss for '{key}' (ns={namespace}), fetching...")
    result = await fetch_func(*args, **kwargs)
    set_cached(store, key, result, namespace=namespace)
    return result


def invalidate_groups(
    store: dict,
    groups_map: Dict[str, Optional[List[str]]],
    *groups: str,
    namespace: str = "_cache",
) -> None:
    """Invalidate cache keys by group name(s).

    Args:
        store: Dict-like object
        groups_map: Mapping of group name -> list of cache keys (or None for "all")
        *groups: One or more group names or individual cache keys
        namespace: Dict key holding the cache sub-dict
    """
    for group in groups:
        if group == "all":
            clear_cache(store, namespace=namespace)
            logger.debug(f"Cache fully cleared (ns={namespace})")
            return

        keys = groups_map.get(group, [group])  # Fallback to group as key
        if keys is None:
            clear_cache(store, namespace=namespace)
            logger.debug(f"Cache fully cleared via group '{group}' (ns={namespace})")
            return
        for key in keys:
            clear_cache(store, key, namespace=namespace)
        logger.debug(f"Invalidated group '{group}': {keys} (ns={namespace})")


def invalidates(
    *groups: str,
    groups_map: Dict[str, Optional[List[str]]],
    namespace: str = "_cache",
):
    """Decorator that invalidates cache groups after handler execution.

    Args:
        *groups: Cache groups to invalidate after the handler runs
        groups_map: Mapping of group name -> list of cache keys
        namespace: Dict key holding the cache sub-dict
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)

            # Find context in args (usually second arg for handlers)
            context = None
            for arg in args:
                if hasattr(arg, "user_data"):
                    context = arg
                    break

            if context:
                invalidate_groups(
                    context.user_data, groups_map, *groups, namespace=namespace
                )

            return result

        return wrapper

    return decorator

"""Deep link utilities for dashboard server registration.

Uses compact pipe-delimited format to stay under Telegram's 64-char limit.
Format: name|host|port|username|password (base64 encoded)
"""

import base64
from typing import Optional, Tuple


def decode_deeplink(encoded: str) -> Tuple[Optional[dict], Optional[str]]:
    """
    Decode a compact deep link payload.

    Args:
        encoded: Base64 encoded payload (name|host|port|user|pass)

    Returns:
        Tuple of (data_dict, error_message)
        If successful, error_message is None
        If failed, data_dict is None
    """
    try:
        # Add padding if needed and decode base64
        padded = encoded + '=' * (4 - len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode('utf-8')

        # Split pipe-delimited fields
        parts = decoded.split('|')
        if len(parts) != 5:
            return None, f"Invalid payload format (expected 5 fields, got {len(parts)})"

        name, host, port, username, password = parts

        # Validate port
        try:
            port = int(port)
        except ValueError:
            return None, "Invalid port number"

        return {
            'name': name,
            'host': host,
            'port': port,
            'username': username,
            'password': password,
        }, None

    except Exception as e:
        return None, f"Decode error: {str(e)}"

"""
Service status command handler
"""

import logging
import asyncio
import time
from telegram import Update
from telegram.ext import ContextTypes

from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


async def check_api_server_status(server_name: str, url: str, username: str, password: str) -> dict:
    """
    Check if an API server is reachable and responding

    Returns:
        dict with status info (available, response_time, error)
    """
    try:
        import aiohttp

        start_time = time.time()

        # Try to connect to the server
        async with aiohttp.ClientSession() as session:
            # Try the /health or root endpoint
            auth = aiohttp.BasicAuth(username, password)

            try:
                async with session.get(
                    f"{url}/",
                    auth=auth,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    response_time = (time.time() - start_time) * 1000  # ms

                    if response.status in [200, 401, 403]:  # 401/403 means server is up but auth might be wrong
                        return {
                            "server_name": server_name,
                            "available": True,
                            "response_time": response_time,
                            "status_code": response.status,
                            "error": None
                        }
                    else:
                        return {
                            "server_name": server_name,
                            "available": False,
                            "response_time": response_time,
                            "status_code": response.status,
                            "error": f"HTTP {response.status}"
                        }
            except asyncio.TimeoutError:
                return {
                    "server_name": server_name,
                    "available": False,
                    "response_time": 5000,
                    "status_code": None,
                    "error": "Timeout (5s)"
                }
            except Exception as e:
                return {
                    "server_name": server_name,
                    "available": False,
                    "response_time": (time.time() - start_time) * 1000,
                    "status_code": None,
                    "error": str(e)
                }

    except Exception as e:
        logger.error(f"Error checking server {server_name}: {e}")
        return {
            "server_name": server_name,
            "available": False,
            "response_time": 0,
            "status_code": None,
            "error": str(e)
        }


@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /status command - Show service status

    Displays status of all configured API servers:
    - Connection status (available/unavailable)
    - Response time
    - Enabled/disabled status
    """
    # Send initial "checking" message
    status_message = await update.message.reply_text(
        "🔍 *Checking service status\\.\\.\\.*",
        parse_mode="MarkdownV2"
    )

    try:
        from servers import server_manager

        servers = server_manager.list_servers()

        if not servers:
            await status_message.edit_text(
                "📊 *Service Status*\n\n"
                "No API servers configured\\.\n\n"
                "_Edit `servers.yml` to configure API servers\\._",
                parse_mode="MarkdownV2"
            )
            return

        # Check all servers in parallel
        check_tasks = []
        for server_name, server_config in servers.items():
            url = f"http://{server_config['host']}:{server_config['port']}"
            task = check_api_server_status(
                server_name=server_name,
                url=url,
                username=server_config['username'],
                password=server_config.get('password', '')
            )
            check_tasks.append(task)

        results = await asyncio.gather(*check_tasks)

        # Build status message
        status_lines = []
        available_count = 0
        total_count = len(results)

        for result in results:
            server_name = result["server_name"]
            server_config = servers[server_name]

            if result["available"]:
                available_count += 1
                status_icon = "🟢"
                status_text = f"Online \\({result['response_time']:.0f}ms\\)"
            else:
                status_icon = "🔴"
                error_msg = escape_markdown_v2(result["error"] or "Unknown error")
                status_text = f"Offline \\- {error_msg}"

            server_name_escaped = escape_markdown_v2(server_name)
            url = f"{server_config['host']}:{server_config['port']}"
            url_escaped = escape_markdown_v2(url)

            status_lines.append(
                f"{status_icon} *{server_name_escaped}*\n"
                f"   `{url_escaped}`\n"
                f"   {status_text}"
            )

        # Overall status
        if available_count == total_count:
            overall_icon = "✅"
            overall_status = "All services online"
        elif available_count > 0:
            overall_icon = "⚠️"
            overall_status = f"{available_count}/{total_count} services online"
        else:
            overall_icon = "❌"
            overall_status = "All services offline"

        message_text = (
            f"📊 *Service Status*\n\n"
            f"{overall_icon} *{escape_markdown_v2(overall_status)}*\n\n"
            + "\n\n".join(status_lines)
        )

        await status_message.edit_text(
            message_text,
            parse_mode="MarkdownV2"
        )

    except Exception as e:
        logger.error(f"Error in status command: {e}", exc_info=True)
        error_text = f"❌ Error checking status: {escape_markdown_v2(str(e))}"
        await status_message.edit_text(error_text, parse_mode="MarkdownV2")

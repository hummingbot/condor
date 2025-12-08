"""
Gateway deployment, lifecycle, and logs management
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..server_context import build_config_message_header
from ._shared import logger, escape_markdown_v2


async def start_deploy_gateway(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Docker image selection for Gateway deployment"""
    try:
        header, server_online, _ = await build_config_message_header(
            "🚀 Deploy Gateway",
            include_gateway=False
        )

        if not server_online:
            message_text = (
                header +
                "⚠️ _Server is offline\\. Cannot deploy Gateway\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        else:
            message_text = (
                header +
                "*Select Docker Image:*\n\n"
                "Choose which Gateway image to deploy\\.\n"
                "The latest stable version is recommended\\."
            )

            keyboard = [
                [InlineKeyboardButton("hummingbot/gateway:latest (recommended)", callback_data="gateway_deploy_image_latest")],
                [InlineKeyboardButton("hummingbot/gateway:development", callback_data="gateway_deploy_image_development")],
                [InlineKeyboardButton("✏️ Custom Image", callback_data="gateway_deploy_custom")],
                [InlineKeyboardButton("« Back", callback_data="config_gateway")],
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing deploy options: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def deploy_gateway_with_image(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deploy Gateway container with selected Docker image"""
    try:
        from servers import server_manager
        from .menu import show_gateway_menu

        # Extract image tag from callback data
        image_tag = query.data.replace("gateway_deploy_image_", "")
        docker_image = f"hummingbot/gateway:{image_tag}"

        await query.answer("🚀 Deploying Gateway...")

        client = await server_manager.get_default_client()

        # Gateway configuration
        config = {
            "image": docker_image,
            "port": 15888,
            "passphrase": "a",
            "dev_mode": True,
        }

        response = await client.gateway.start(config)

        if response.get('status') == 'success' or response.get('status') == 'running':
            await query.answer("✅ Gateway deployed successfully")
        else:
            await query.answer("⚠️ Gateway deployment may need verification")

        # Refresh the gateway menu to show new status
        await show_gateway_menu(query, context)

    except Exception as e:
        logger.error(f"Error deploying gateway: {e}", exc_info=True)
        await query.answer(f"❌ Deployment failed: {str(e)[:100]}")
        # Still refresh menu to show current state
        from .menu import show_gateway_menu
        await show_gateway_menu(query, context)


async def prompt_custom_image(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to enter custom Docker image"""
    try:
        header, server_online, _ = await build_config_message_header(
            "✏️ Custom Gateway Image",
            include_gateway=False
        )

        context.user_data['awaiting_gateway_input'] = 'custom_image'
        context.user_data['gateway_message_id'] = query.message.message_id
        context.user_data['gateway_chat_id'] = query.message.chat_id

        message_text = (
            header +
            "*Enter Custom Docker Image:*\n\n"
            "Please send the full Docker image name and tag\\.\n\n"
            "*Examples:*\n"
            "`hummingbot/gateway:1\\.0\\.0`\n"
            "`myregistry\\.io/gateway:custom`"
        )

        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_deploy")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting custom image: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def stop_gateway(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop Gateway container on the default server"""
    try:
        from servers import server_manager
        from .menu import show_gateway_menu

        await query.answer("⏹ Stopping Gateway...")

        client = await server_manager.get_default_client()
        response = await client.gateway.stop()

        if response.get('status') == 'success' or response.get('status') == 'stopped':
            await query.answer("✅ Gateway stopped successfully")
        else:
            await query.answer("⚠️ Gateway stop may need verification")

        # Refresh the gateway menu
        await show_gateway_menu(query, context)

    except Exception as e:
        logger.error(f"Error stopping gateway: {e}", exc_info=True)
        await query.answer(f"❌ Stop failed: {str(e)[:100]}")
        from .menu import show_gateway_menu
        await show_gateway_menu(query, context)


async def restart_gateway(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart Gateway container on the default server"""
    try:
        from servers import server_manager
        from .menu import show_gateway_menu
        import asyncio

        # Answer the callback query first
        await query.answer("🔄 Restarting Gateway...")

        # Update message to show restarting status
        header, _, _ = await build_config_message_header(
            "🌐 Gateway Configuration",
            include_gateway=False  # Don't check status during restart
        )

        restarting_text = (
            header +
            "🔄 *Restarting Gateway\\.\\.\\.*\n\n"
            "_Please wait, this may take a few moments\\._"
        )

        try:
            await query.message.edit_text(
                restarting_text,
                parse_mode="MarkdownV2"
            )
        except:
            pass  # Ignore if message can't be edited

        # Perform the restart
        client = await server_manager.get_default_client()
        response = await client.gateway.restart()

        # Wait a moment for the restart to take effect
        await asyncio.sleep(2)

        # Show success message briefly
        success = response.get('status') == 'success' or response.get('status') == 'running'
        status_text = (
            header +
            ("✅ *Gateway Restarted Successfully*\n\n"
             "_Refreshing menu\\.\\.\\._" if success else
             "⚠️ *Gateway Restart Completed*\n\n"
             "_Verifying status\\.\\.\\._")
        )

        try:
            await query.message.edit_text(
                status_text,
                parse_mode="MarkdownV2"
            )
        except:
            pass  # Ignore if message can't be edited

        # Brief pause to show the status
        await asyncio.sleep(1)

        # Refresh the gateway menu
        await show_gateway_menu(query, context)

    except Exception as e:
        logger.error(f"Error restarting gateway: {e}", exc_info=True)
        try:
            await query.answer(f"❌ Restart failed: {str(e)[:100]}")
        except:
            pass  # Query might have expired
        from .menu import show_gateway_menu
        try:
            await show_gateway_menu(query, context)
        except:
            pass  # Best effort to show menu


async def show_gateway_logs(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Gateway container logs"""
    try:
        from servers import server_manager

        await query.answer("📋 Loading logs...")

        client = await server_manager.get_default_client()
        response = await client.gateway.get_logs(tail=50)

        logs = response.get('logs', 'No logs available')

        # Truncate logs if too long for Telegram
        if len(logs) > 3500:
            logs = logs[-3500:]
            logs = "...\\(truncated\\)\n" + logs

        logs_escaped = escape_markdown_v2(logs)

        message_text = (
            "📋 *Gateway Logs* \\(last 50 lines\\)\n\n"
            f"```\n{logs_escaped}\n```"
        )

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="gateway_logs")],
            [InlineKeyboardButton("« Back", callback_data="config_gateway")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            error_str = str(e)
            if "Message is not modified" in error_str:
                # Try to answer the query, but don't raise if it fails
                try:
                    await query.answer("✅ Already up to date")
                except:
                    pass  # Query expired, ignore
            elif "Query is too old" in error_str or "query id is invalid" in error_str:
                # Query expired, message was likely already updated - ignore
                pass
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing gateway logs: {e}", exc_info=True)
        error_text = f"❌ Error loading logs: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)

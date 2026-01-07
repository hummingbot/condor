"""
Gateway deployment, lifecycle, and logs management
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..server_context import build_config_message_header
from ._shared import logger, escape_markdown_v2


async def start_deploy_gateway(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Docker image selection for Gateway deployment"""
    try:
        chat_id = query.message.chat_id
        header, server_online, _ = await build_config_message_header(
            "🚀 Deploy Gateway",
            include_gateway=False,
            chat_id=chat_id
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
    """Prompt for passphrase before deploying Gateway"""
    try:
        # Extract image tag from callback data
        image_tag = query.data.replace("gateway_deploy_image_", "")
        docker_image = f"hummingbot/gateway:{image_tag}"

        # Store image and prompt for passphrase
        context.user_data['gateway_deploy_image'] = docker_image
        await prompt_passphrase(query, context)

    except Exception as e:
        logger.error(f"Error starting deploy flow: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_passphrase(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to enter passphrase for Gateway deployment"""
    try:
        chat_id = query.message.chat_id
        header, server_online, _ = await build_config_message_header(
            "🔐 Gateway Passphrase",
            include_gateway=False,
            chat_id=chat_id
        )

        docker_image = context.user_data.get('gateway_deploy_image', 'hummingbot/gateway:latest')
        image_escaped = escape_markdown_v2(docker_image)

        context.user_data['awaiting_gateway_input'] = 'passphrase'
        context.user_data['gateway_message_id'] = query.message.message_id
        context.user_data['gateway_chat_id'] = query.message.chat_id

        message_text = (
            header +
            f"*Image:* `{image_escaped}`\n\n"
            "*Enter Gateway Passphrase:*\n\n"
            "This passphrase is used by Gateway to encrypt stored wallet keys\\.\n\n"
            "_Please send your passphrase as a message\\._"
        )

        keyboard = [[InlineKeyboardButton("« Cancel", callback_data="gateway_deploy")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting passphrase: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def execute_gateway_deploy(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, docker_image: str, passphrase: str) -> None:
    """Execute the actual Gateway deployment with provided config"""
    from config_manager import get_config_manager
    from .menu import show_gateway_menu

    try:
        client = await get_config_manager().get_client_for_chat(chat_id)

        # Gateway configuration
        config = {
            "image": docker_image,
            "port": 15888,
            "passphrase": passphrase,
            "dev_mode": True,
        }

        # Show deploying message
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="🚀 *Deploying Gateway\\.\\.\\.*\n\n_Please wait, this may take a moment\\._",
            parse_mode="MarkdownV2"
        )

        response = await client.gateway.start(config)

        success = response.get('status') == 'success' or response.get('status') == 'running'

        if success:
            result_text = "✅ *Gateway Deployed Successfully*\n\n_Returning to menu\\.\\.\\._"
        else:
            result_text = "⚠️ *Gateway Deployment Completed*\n\n_Verifying status\\.\\.\\._"

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=result_text,
            parse_mode="MarkdownV2"
        )

        # Brief pause then show menu
        import asyncio
        await asyncio.sleep(1)

        # Create a mock query object for show_gateway_menu
        class MockQuery:
            def __init__(self, message):
                self.message = message

            async def answer(self, *args, **kwargs):
                pass

        class MockMessage:
            def __init__(self, chat_id, message_id, bot):
                self.chat_id = chat_id
                self.message_id = message_id
                self._bot = bot

            async def edit_text(self, text, **kwargs):
                await self._bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=text,
                    **kwargs
                )

        mock_message = MockMessage(chat_id, message_id, context.bot)
        mock_query = MockQuery(mock_message)
        await show_gateway_menu(mock_query, context)

    except Exception as e:
        logger.error(f"Error deploying gateway: {e}", exc_info=True)
        error_text = f"❌ *Deployment Failed*\n\n`{escape_markdown_v2(str(e))}`"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=error_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


async def prompt_custom_image(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to enter custom Docker image"""
    try:
        chat_id = query.message.chat_id
        header, server_online, _ = await build_config_message_header(
            "✏️ Custom Gateway Image",
            include_gateway=False,
            chat_id=chat_id
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
    """Stop Gateway container on the current server"""
    try:
        from config_manager import get_config_manager
        from .menu import show_gateway_menu

        await query.answer("⏹ Stopping Gateway...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)
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
    """Restart Gateway container on the current server"""
    try:
        from config_manager import get_config_manager
        from .menu import show_gateway_menu
        import asyncio

        chat_id = query.message.chat_id

        # Answer the callback query first
        await query.answer("🔄 Restarting Gateway...")

        # Update message to show restarting status
        header, _, _ = await build_config_message_header(
            "🌐 Gateway Configuration",
            include_gateway=False,  # Don't check status during restart
            chat_id=chat_id
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
        client = await get_config_manager().get_client_for_chat(chat_id)
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
        from config_manager import get_config_manager

        await query.answer("📋 Loading logs...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)
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


async def handle_deployment_input(update, context) -> None:
    """Handle text input during gateway deployment flow"""
    from telegram import Update
    from telegram.ext import ContextTypes

    awaiting_field = context.user_data.get('awaiting_gateway_input')
    if not awaiting_field:
        return

    # Delete user's input message for security (passphrase shouldn't be visible)
    try:
        await update.message.delete()
    except:
        pass

    try:
        message_id = context.user_data.get('gateway_message_id')
        chat_id = context.user_data.get('gateway_chat_id')

        if awaiting_field == 'passphrase':
            passphrase = update.message.text.strip()
            docker_image = context.user_data.get('gateway_deploy_image', 'hummingbot/gateway:latest')

            # Clear context
            context.user_data.pop('awaiting_gateway_input', None)
            context.user_data.pop('gateway_deploy_image', None)
            context.user_data.pop('gateway_message_id', None)
            context.user_data.pop('gateway_chat_id', None)

            if not passphrase:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Passphrase cannot be empty",
                    parse_mode="MarkdownV2"
                )
                return

            # Execute deployment with provided passphrase
            await execute_gateway_deploy(context, chat_id, message_id, docker_image, passphrase)

        elif awaiting_field == 'custom_image':
            custom_image = update.message.text.strip()

            # Clear context
            context.user_data.pop('awaiting_gateway_input', None)
            context.user_data.pop('gateway_message_id', None)
            context.user_data.pop('gateway_chat_id', None)

            if not custom_image:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Image name cannot be empty",
                    parse_mode="MarkdownV2"
                )
                return

            # Store custom image and prompt for passphrase
            context.user_data['gateway_deploy_image'] = custom_image
            context.user_data['gateway_message_id'] = message_id
            context.user_data['gateway_chat_id'] = chat_id

            # Create mock query for prompt_passphrase
            class MockQuery:
                def __init__(self, message):
                    self.message = message

                async def answer(self, *args, **kwargs):
                    pass

            class MockMessage:
                def __init__(self, chat_id, message_id, bot):
                    self.chat_id = chat_id
                    self.message_id = message_id
                    self._bot = bot

                async def edit_text(self, text, **kwargs):
                    await self._bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.message_id,
                        text=text,
                        **kwargs
                    )

            mock_message = MockMessage(chat_id, message_id, context.bot)
            mock_query = MockQuery(mock_message)
            await prompt_passphrase(mock_query, context)

    except Exception as e:
        logger.error(f"Error handling deployment input: {e}", exc_info=True)
        context.user_data.pop('awaiting_gateway_input', None)

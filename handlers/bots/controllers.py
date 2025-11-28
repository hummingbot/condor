"""
Controller configuration management

Provides:
- List existing controller configs
- Create new controller configs (grid_strike)
- Interactive form for configuration
- Deploy selected controllers
"""

import logging
from typing import Dict, Any, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from ._shared import (
    get_bots_client,
    clear_bots_state,
    get_controller_config,
    set_controller_config,
    init_new_controller_config,
    format_controller_config_summary,
    format_config_field_value,
    SUPPORTED_CONTROLLERS,
    GRID_STRIKE_DEFAULTS,
    GRID_STRIKE_FIELDS,
    GRID_STRIKE_FIELD_ORDER,
)

logger = logging.getLogger(__name__)


# ============================================
# CONTROLLER CONFIGS MENU
# ============================================

async def show_controller_configs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the controller configs management menu

    Options:
    - View existing configs
    - Create new config
    """
    query = update.callback_query

    keyboard = [
        [
            InlineKeyboardButton("View Configs", callback_data="bots:list_configs"),
        ],
        [
            InlineKeyboardButton("+ New Grid Strike", callback_data="bots:new_grid_strike"),
        ],
        [
            InlineKeyboardButton("Back", callback_data="bots:main_menu"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_text = (
        r"*Controller Configs*" + "\n\n"
        r"Manage your trading controller configurations\." + "\n\n"
        r"*View Configs* \- See existing configurations" + "\n"
        r"*New Grid Strike* \- Create a new grid trading config"
    )

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# LIST EXISTING CONFIGS
# ============================================

async def show_configs_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of existing controller configs from the backend"""
    query = update.callback_query

    try:
        client = await get_bots_client()

        # Fetch configs from backend
        configs = await client.controllers.list_controller_configs()

        if not configs:
            keyboard = [
                [InlineKeyboardButton("+ New Grid Strike", callback_data="bots:new_grid_strike")],
                [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.edit_text(
                r"*Controller Configs*" + "\n\n"
                r"No configurations found\." + "\n"
                r"Create a new one to get started\!",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return

        # Store configs for selection
        context.user_data["controller_configs_list"] = configs

        # Build message with config list
        lines = [r"*Existing Controller Configs*", ""]

        for i, config in enumerate(configs):
            config_id = config.get("id", f"config_{i}")
            controller_type = config.get("controller_name", "unknown")
            pair = config.get("trading_pair", "N/A")
            side = "LONG" if config.get("side", 1) == 1 else "SHORT"

            lines.append(f"{i+1}\\. `{escape_markdown_v2(config_id)}`")
            lines.append(f"   {escape_markdown_v2(controller_type)} \\| {escape_markdown_v2(pair)} \\| {side}")
            lines.append("")

        # Build keyboard with config buttons
        keyboard = []

        # Config selection buttons (show first 5)
        for i, config in enumerate(configs[:5]):
            config_id = config.get("id", f"config_{i}")
            keyboard.append([
                InlineKeyboardButton(f"Edit: {config_id[:20]}", callback_data=f"bots:edit_config:{i}")
            ])

        keyboard.extend([
            [InlineKeyboardButton("+ New Grid Strike", callback_data="bots:new_grid_strike")],
            [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error fetching configs: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to fetch configs: {str(e)}")

        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:controller_configs")]]

        await query.message.edit_text(
            error_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ============================================
# CREATE NEW CONFIG FORM
# ============================================

async def show_new_grid_strike_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the form for creating a new Grid Strike config"""
    query = update.callback_query

    # Initialize new config with defaults
    config = init_new_controller_config(context, "grid_strike")
    context.user_data["bots_state"] = "editing_config"

    await show_config_form(update, context)


async def show_config_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the configuration form with current values"""
    query = update.callback_query
    config = get_controller_config(context)

    if not config:
        config = init_new_controller_config(context, "grid_strike")

    # Build the form display
    lines = [r"*Grid Strike Configuration*", ""]

    # Show current values
    for field_name in GRID_STRIKE_FIELD_ORDER:
        field_info = GRID_STRIKE_FIELDS[field_name]
        label = field_info["label"]

        # Get value, handling nested triple_barrier_config
        if field_name == "take_profit":
            value = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
        else:
            value = config.get(field_name, "")

        formatted_value = format_config_field_value(field_name, value)
        required = "*" if field_info.get("required") else ""

        lines.append(f"*{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(formatted_value)}`")

    lines.append("")
    lines.append(r"_Tap a button to edit a field\. \* = required_")

    # Build keyboard with field buttons
    keyboard = []

    # Row 1: ID and Connector
    keyboard.append([
        InlineKeyboardButton("ID", callback_data="bots:set_field:id"),
        InlineKeyboardButton("Connector", callback_data="bots:set_field:connector_name"),
        InlineKeyboardButton("Pair", callback_data="bots:set_field:trading_pair"),
    ])

    # Row 2: Side and Leverage
    keyboard.append([
        InlineKeyboardButton("Side", callback_data="bots:toggle_side"),
        InlineKeyboardButton("Leverage", callback_data="bots:set_field:leverage"),
        InlineKeyboardButton("Amount", callback_data="bots:set_field:total_amount_quote"),
    ])

    # Row 3: Prices
    keyboard.append([
        InlineKeyboardButton("Start Price", callback_data="bots:set_field:start_price"),
        InlineKeyboardButton("End Price", callback_data="bots:set_field:end_price"),
        InlineKeyboardButton("Limit Price", callback_data="bots:set_field:limit_price"),
    ])

    # Row 4: Advanced
    keyboard.append([
        InlineKeyboardButton("Max Orders", callback_data="bots:set_field:max_open_orders"),
        InlineKeyboardButton("Min Spread", callback_data="bots:set_field:min_spread_between_orders"),
        InlineKeyboardButton("Take Profit", callback_data="bots:set_field:take_profit"),
    ])

    # Row 5: Actions
    keyboard.append([
        InlineKeyboardButton("Save Config", callback_data="bots:save_config"),
        InlineKeyboardButton("Cancel", callback_data="bots:controller_configs"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# FIELD EDITING
# ============================================

async def handle_set_field(update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str) -> None:
    """Prompt user to enter a value for a field

    Args:
        update: Telegram update
        context: Telegram context
        field_name: Name of the field to edit
    """
    query = update.callback_query

    field_info = GRID_STRIKE_FIELDS.get(field_name, {})
    label = field_info.get("label", field_name)
    hint = field_info.get("hint", "")
    field_type = field_info.get("type", "str")

    # Set state for text input
    context.user_data["bots_state"] = f"set_field:{field_name}"
    context.user_data["editing_controller_field"] = field_name

    # Get current value
    config = get_controller_config(context)
    if field_name == "take_profit":
        current = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    else:
        current = config.get(field_name, "")

    current_str = format_config_field_value(field_name, current)

    message = (
        f"*Set {escape_markdown_v2(label)}*\n\n"
        f"Current: `{escape_markdown_v2(current_str)}`\n\n"
    )

    if hint:
        message += f"_Hint: {escape_markdown_v2(hint)}_\n\n"

    message += r"Type the new value or tap Cancel\."

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="bots:edit_config_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_toggle_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle the side between LONG and SHORT"""
    query = update.callback_query
    config = get_controller_config(context)

    current_side = config.get("side", 1)
    new_side = -1 if current_side == 1 else 1
    config["side"] = new_side
    set_controller_config(context, config)

    # Refresh the form
    await show_config_form(update, context)


async def process_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process user input for a field

    Args:
        update: Telegram update
        context: Telegram context
        user_input: The text the user entered
    """
    field_name = context.user_data.get("editing_controller_field")

    if not field_name:
        await update.message.reply_text("No field selected. Please try again.")
        return

    field_info = GRID_STRIKE_FIELDS.get(field_name, {})
    field_type = field_info.get("type", "str")
    label = field_info.get("label", field_name)

    config = get_controller_config(context)

    try:
        # Parse the value based on type
        if field_type == "int":
            value = int(user_input)
        elif field_type == "float":
            value = float(user_input)
        else:
            value = user_input.strip()

        # Set the value
        if field_name == "take_profit":
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy()
            config["triple_barrier_config"]["take_profit"] = value
        else:
            config[field_name] = value

        set_controller_config(context, config)

        # Clear field editing state
        context.user_data.pop("editing_controller_field", None)
        context.user_data["bots_state"] = "editing_config"

        # Show success and return to form
        await update.message.reply_text(
            f"{label} set to: {value}",
            parse_mode="HTML"
        )

        # Show the form again using a fake callback query
        # We need to edit the previous message, so we'll send a new one
        keyboard = [[InlineKeyboardButton("Continue Editing", callback_data="bots:edit_config_back")]]
        await update.message.reply_text(
            "Value updated\\. Tap to continue editing\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except ValueError as e:
        await update.message.reply_text(
            f"Invalid value for {label}. Expected {field_type}. Please try again."
        )


# ============================================
# SAVE CONFIG
# ============================================

async def handle_save_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the current config to the backend"""
    query = update.callback_query
    config = get_controller_config(context)

    # Validate required fields
    missing = []
    for field_name in GRID_STRIKE_FIELD_ORDER:
        field_info = GRID_STRIKE_FIELDS[field_name]
        if field_info.get("required"):
            if field_name == "take_profit":
                value = config.get("triple_barrier_config", {}).get("take_profit")
            else:
                value = config.get(field_name)

            if value is None or value == "" or value == 0:
                missing.append(field_info["label"])

    if missing:
        missing_str = ", ".join(missing)
        await query.answer(f"Missing required fields: {missing_str}", show_alert=True)
        return

    try:
        client = await get_bots_client()

        # Save to backend using config id as the config_name
        config_name = config.get("id", "")
        result = await client.controllers.create_or_update_controller_config(config_name, config)

        # Clear state
        clear_bots_state(context)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_grid_strike")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        config_id = config.get("id", "unknown")
        await query.message.edit_text(
            f"*Config Saved\\!*\n\n"
            f"Controller `{escape_markdown_v2(config_id)}` has been saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error saving config: {e}", exc_info=True)
        await query.answer(f"Failed to save: {str(e)[:100]}", show_alert=True)


# ============================================
# EDIT EXISTING CONFIG
# ============================================

async def handle_edit_config(update: Update, context: ContextTypes.DEFAULT_TYPE, config_index: int) -> None:
    """Load an existing config for editing

    Args:
        update: Telegram update
        context: Telegram context
        config_index: Index in the configs list
    """
    query = update.callback_query
    configs_list = context.user_data.get("controller_configs_list", [])

    if config_index >= len(configs_list):
        await query.answer("Config not found", show_alert=True)
        return

    config = configs_list[config_index].copy()
    set_controller_config(context, config)
    context.user_data["bots_state"] = "editing_config"

    await show_config_form(update, context)


# ============================================
# DEPLOY CONTROLLERS
# ============================================

# Default deploy settings
DEPLOY_DEFAULTS = {
    "instance_name": "",
    "credentials_profile": "",
    "controllers_config": [],
    "max_global_drawdown_quote": None,
    "max_controller_drawdown_quote": None,
    "image": "hummingbot/hummingbot:latest",
}


async def show_deploy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the deploy controllers menu"""
    query = update.callback_query

    try:
        client = await get_bots_client()
        configs = await client.controllers.list_controller_configs()

        if not configs:
            keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
            await query.message.edit_text(
                r"*Deploy Controllers*" + "\n\n"
                r"No configurations available to deploy\." + "\n"
                r"Create a controller config first\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Store configs and initialize selection
        context.user_data["controller_configs_list"] = configs
        selected = context.user_data.get("selected_controllers", set())

        # Build message
        lines = [r"*Deploy Controllers*", ""]
        lines.append(r"Select controllers to deploy:")
        lines.append("")

        # Build keyboard with checkboxes
        keyboard = []

        for i, config in enumerate(configs):
            config_id = config.get("id", config.get("config_name", f"config_{i}"))
            is_selected = i in selected
            checkbox = "[x]" if is_selected else "[ ]"

            keyboard.append([
                InlineKeyboardButton(
                    f"{checkbox} {config_id[:25]}",
                    callback_data=f"bots:toggle_deploy:{i}"
                )
            ])

        # Action buttons
        keyboard.append([
            InlineKeyboardButton("Select All", callback_data="bots:select_all"),
            InlineKeyboardButton("Clear All", callback_data="bots:clear_all"),
        ])

        if selected:
            keyboard.append([
                InlineKeyboardButton(f"Next: Configure ({len(selected)})", callback_data="bots:deploy_configure"),
            ])

        keyboard.append([
            InlineKeyboardButton("Back", callback_data="bots:main_menu"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error loading deploy menu: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to load configs: {str(e)}")
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            error_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_toggle_deploy_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int) -> None:
    """Toggle selection of a controller for deployment"""
    selected = context.user_data.get("selected_controllers", set())

    if index in selected:
        selected.discard(index)
    else:
        selected.add(index)

    context.user_data["selected_controllers"] = selected
    await show_deploy_menu(update, context)


async def handle_select_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Select all controllers for deployment"""
    configs = context.user_data.get("controller_configs_list", [])
    context.user_data["selected_controllers"] = set(range(len(configs)))
    await show_deploy_menu(update, context)


async def handle_clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all selections"""
    context.user_data["selected_controllers"] = set()
    await show_deploy_menu(update, context)


async def show_deploy_configure(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the deployment configuration form"""
    query = update.callback_query

    selected = context.user_data.get("selected_controllers", set())
    configs = context.user_data.get("controller_configs_list", [])

    if not selected:
        await query.answer("No controllers selected", show_alert=True)
        return

    # Get selected config names
    controller_names = [
        configs[i].get("id", configs[i].get("config_name", f"config_{i}"))
        for i in selected if i < len(configs)
    ]

    # Initialize or get deploy params
    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
    deploy_params["controllers_config"] = controller_names
    context.user_data["deploy_params"] = deploy_params

    await show_deploy_form(update, context)


async def show_deploy_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the deployment configuration form with current values"""
    query = update.callback_query
    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())

    # Build display
    lines = [r"*Deploy Configuration*", ""]

    instance = deploy_params.get("instance_name", "") or "Not set"
    creds = deploy_params.get("credentials_profile", "") or "Not set"
    controllers = deploy_params.get("controllers_config", [])
    controllers_str = ", ".join(controllers) if controllers else "None"
    max_global = deploy_params.get("max_global_drawdown_quote")
    max_controller = deploy_params.get("max_controller_drawdown_quote")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")

    lines.append(f"*Instance Name*\\*: `{escape_markdown_v2(instance)}`")
    lines.append(f"*Credentials Profile*\\*: `{escape_markdown_v2(creds)}`")
    lines.append(f"*Controllers*: `{escape_markdown_v2(controllers_str[:50])}`")
    lines.append(f"*Max Global DD*: `{max_global if max_global else 'Not set'}`")
    lines.append(f"*Max Controller DD*: `{max_controller if max_controller else 'Not set'}`")
    lines.append(f"*Image*: `{escape_markdown_v2(image)}`")
    lines.append("")
    lines.append(r"_\* = required_")

    # Build keyboard
    keyboard = [
        [
            InlineKeyboardButton("Instance Name", callback_data="bots:deploy_set:instance_name"),
            InlineKeyboardButton("Credentials", callback_data="bots:deploy_set:credentials_profile"),
        ],
        [
            InlineKeyboardButton("Max Global DD", callback_data="bots:deploy_set:max_global_drawdown_quote"),
            InlineKeyboardButton("Max Controller DD", callback_data="bots:deploy_set:max_controller_drawdown_quote"),
        ],
        [
            InlineKeyboardButton("Image", callback_data="bots:deploy_set:image"),
        ],
    ]

    # Check if ready to deploy
    can_deploy = bool(deploy_params.get("instance_name") and deploy_params.get("credentials_profile"))

    if can_deploy:
        keyboard.append([
            InlineKeyboardButton("Deploy Now", callback_data="bots:execute_deploy"),
        ])

    keyboard.append([
        InlineKeyboardButton("Back to Selection", callback_data="bots:deploy_menu"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_deploy_set_field(update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str) -> None:
    """Prompt user to enter a value for a deploy field"""
    query = update.callback_query

    labels = {
        "instance_name": "Instance Name",
        "credentials_profile": "Credentials Profile",
        "max_global_drawdown_quote": "Max Global Drawdown (Quote)",
        "max_controller_drawdown_quote": "Max Controller Drawdown (Quote)",
        "image": "Docker Image",
    }

    hints = {
        "instance_name": "e.g. my_grid_bot",
        "credentials_profile": "e.g. binance_main",
        "max_global_drawdown_quote": "e.g. 1000 (in USDT)",
        "max_controller_drawdown_quote": "e.g. 500 (in USDT)",
        "image": "e.g. hummingbot/hummingbot:latest",
    }

    label = labels.get(field_name, field_name)
    hint = hints.get(field_name, "")

    # Set state for text input
    context.user_data["bots_state"] = f"deploy_set:{field_name}"
    context.user_data["editing_deploy_field"] = field_name

    # Get current value
    deploy_params = context.user_data.get("deploy_params", {})
    current = deploy_params.get(field_name, "")
    current_str = str(current) if current else "Not set"

    message = (
        f"*Set {escape_markdown_v2(label)}*\n\n"
        f"Current: `{escape_markdown_v2(current_str)}`\n\n"
    )

    if hint:
        message += f"_Hint: {escape_markdown_v2(hint)}_\n\n"

    message += r"Type the new value or tap Cancel\."

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="bots:deploy_form_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def process_deploy_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process user input for a deploy field"""
    field_name = context.user_data.get("editing_deploy_field")

    if not field_name:
        await update.message.reply_text("No field selected. Please try again.")
        return

    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())

    try:
        # Parse the value based on field type
        if field_name in ["max_global_drawdown_quote", "max_controller_drawdown_quote"]:
            value = float(user_input) if user_input.strip() else None
        else:
            value = user_input.strip()

        # Set the value
        deploy_params[field_name] = value
        context.user_data["deploy_params"] = deploy_params

        # Clear field editing state
        context.user_data.pop("editing_deploy_field", None)
        context.user_data["bots_state"] = "deploy_configure"

        # Show confirmation
        label = field_name.replace("_", " ").title()
        await update.message.reply_text(f"{label} set to: {value}")

        # Show button to return to form
        keyboard = [[InlineKeyboardButton("Continue", callback_data="bots:deploy_form_back")]]
        await update.message.reply_text(
            "Value updated\\. Tap to continue\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except ValueError as e:
        await update.message.reply_text(f"Invalid value. Please enter a valid number.")


async def handle_execute_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the deployment of selected controllers"""
    query = update.callback_query

    deploy_params = context.user_data.get("deploy_params", {})

    instance_name = deploy_params.get("instance_name")
    credentials_profile = deploy_params.get("credentials_profile")
    controllers_config = deploy_params.get("controllers_config", [])

    if not instance_name or not credentials_profile:
        await query.answer("Instance name and credentials are required", show_alert=True)
        return

    if not controllers_config:
        await query.answer("No controllers selected", show_alert=True)
        return

    try:
        client = await get_bots_client()

        # Deploy using deploy_v2_controllers
        result = await client.bot_orchestration.deploy_v2_controllers(
            instance_name=instance_name,
            credentials_profile=credentials_profile,
            controllers_config=controllers_config,
            max_global_drawdown_quote=deploy_params.get("max_global_drawdown_quote"),
            max_controller_drawdown_quote=deploy_params.get("max_controller_drawdown_quote"),
            image=deploy_params.get("image", "hummingbot/hummingbot:latest"),
        )

        # Clear deploy state
        context.user_data.pop("selected_controllers", None)
        context.user_data.pop("deploy_params", None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton("View Bots", callback_data="bots:main_menu")],
            [InlineKeyboardButton("Deploy More", callback_data="bots:deploy_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        controllers_str = ", ".join([f"`{escape_markdown_v2(c)}`" for c in controllers_config])

        status = result.get("status", "unknown")
        if status == "success":
            await query.message.edit_text(
                f"*Deployment Started\\!*\n\n"
                f"*Instance:* `{escape_markdown_v2(instance_name)}`\n"
                f"*Controllers:*\n{controllers_str}\n\n"
                f"The bot is being deployed\\. Check status in Bots menu\\.",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            error_msg = result.get("message", "Unknown error")
            await query.message.edit_text(
                f"*Deployment Failed*\n\n"
                f"Error: {escape_markdown_v2(error_msg)}",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error deploying controllers: {e}", exc_info=True)
        await query.answer(f"Deploy failed: {str(e)[:100]}", show_alert=True)

"""
Executor preferences manager for storing user defaults in markdown format.

This module manages user preferences for executor configurations stored in
a human-readable markdown file with embedded YAML blocks.

Preferences are stored at: ~/.hummingbot_mcp/executor_preferences.md
"""

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("hummingbot-mcp")

# Default preferences directory and file
PREFERENCES_DIR = Path.home() / ".hummingbot_mcp"
PREFERENCES_FILE = PREFERENCES_DIR / "executor_preferences.md"

# Default template for the preferences file
DEFAULT_PREFERENCES_TEMPLATE = """# Executor Preferences

This file stores your default configurations for different executor types.
You can edit this file manually or use `save_as_default=true` when creating executors.

A default is applied to every later create that omits that key, on every pair.
Keep reusable settings here (leverage, order types, spreads, keep_position);
never a trading pair, a side, an amount or a price. `save_as_default` stores
only the reusable settings.

---

## Your Default Configurations

Edit the YAML blocks below to set your preferred defaults for each executor type.
These defaults will be applied when creating new executors.

### Position Executor Defaults

```yaml
position_executor:
  # Add your default position executor config here
  # Example:
  # leverage: 10
  # triple_barrier_config:
  #   open_order_type: 3  # LIMIT_MAKER
```

### DCA Executor Defaults

```yaml
dca_executor:
  # Add your default DCA executor config here
  # Example:
  # leverage: 1
  # mode: MAKER
```

### Grid Executor Defaults

Note: The example below is a tight scalping configuration. Nothing in it is
active until you uncomment it: a key left commented is not sent, and the
backend's own default applies.

```yaml
grid_executor:
  # Add your default grid executor config here
  # Example:
  # min_spread_between_orders: 0.0001  # 0.01% between levels
  # max_open_orders: 15
  # activation_bounds: 0.001  # 0.1% — only place orders near current price
  # order_frequency: 5  # seconds between order batches
  # max_orders_per_batch: 1
  # keep_position: true
  # coerce_tp_to_step: true
  # triple_barrier_config:
  #   take_profit: 0.0002  # 0.02%
  #   open_order_type: 3  # LIMIT_MAKER (post-only, earns maker fees)
  #   take_profit_order_type: 3  # LIMIT_MAKER
```

### Order Executor Defaults

```yaml
order_executor:
  # Add your default order executor config here
  # Example:
  # leverage: 1
```

### Lp Executor Defaults

```yaml
lp_executor:
  # Set your preferred defaults here (all optional):
  # swap_provider: jupiter/router  # optional — defaults to network's default swap provider
  # keep_position: false
  # extra_params:
  #   strategyType: 0  # Meteora only: 0=Spot, 1=Curve, 2=Bid-Ask
```

---

*Last updated: Never*
"""


def _merge_one_level(
    defaults: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Merge ``overrides`` over ``defaults``, descending one level into nested blocks.

    A plain ``{**defaults, **overrides}`` replaces a nested block wholesale, so a
    create that sends ``triple_barrier_config={}`` (the grid tool always sends the
    block, because the backend schema requires it) or only ``{"take_profit": ...}``
    silently wiped every other barrier the user had saved — deploying a different
    strategy from the one on file, with no message saying so. Where a key is a dict
    on BOTH sides the two are merged instead, with the override still winning
    key-by-key. Scalars and lists keep replace semantics: a saved list is a whole
    value, not something to append to.
    """
    merged = {**defaults, **overrides}
    for key, default_value in defaults.items():
        override_value = overrides.get(key)
        if isinstance(default_value, dict) and isinstance(override_value, dict):
            merged[key] = {**default_value, **override_value}
    return merged


class ExecutorPreferencesManager:
    """Manager for executor preferences stored in markdown format."""

    def __init__(self, preferences_path: Path | None = None):
        """Initialize the preferences manager.

        Args:
            preferences_path: Custom path for preferences file. Defaults to ~/.hummingbot_mcp/executor_preferences.md
        """
        self.preferences_path = preferences_path or PREFERENCES_FILE
        self._ensure_preferences_exist()

    def _ensure_preferences_exist(self) -> None:
        """Create preferences directory and file if they don't exist."""
        # Create directory if it doesn't exist
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)

        # Create default preferences file if it doesn't exist
        if not self.preferences_path.exists():
            self._write_template()
            logger.info(
                f"Created default executor preferences at {self.preferences_path}"
            )

    def _write_template(self) -> None:
        """Write the default template to the preferences file."""
        self._write_content(DEFAULT_PREFERENCES_TEMPLATE)

    def _read_content(self) -> str:
        """Read the preferences file content."""
        if not self.preferences_path.exists():
            self._write_template()
        return self.preferences_path.read_text()

    def _write_content(self, content: str) -> None:
        """Write content to the preferences file, atomically.

        The file is shared by every MCP subprocess of this OS user, and each of
        them re-reads it on every create. A plain ``write_text`` truncates first,
        so a create racing a save could parse half a file and silently drop
        every default in it; a temp file renamed into place is seen whole or not
        at all.
        """
        directory = self.preferences_path.parent
        fd, tmp_name = tempfile.mkstemp(
            dir=directory, prefix=f".{self.preferences_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
            os.replace(tmp_name, self.preferences_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _parse_yaml_blocks(self, content: str) -> dict[str, dict[str, Any]]:
        """Parse YAML blocks from markdown content.

        Args:
            content: Markdown content with embedded YAML blocks

        Returns:
            Dictionary mapping executor type to its configuration
        """
        # Pattern to match YAML code blocks
        yaml_pattern = r"```yaml\s*\n([\s\S]*?)```"

        defaults = {}
        matches = re.findall(yaml_pattern, content)

        for yaml_content in matches:
            try:
                parsed = yaml.safe_load(yaml_content)
                if parsed and isinstance(parsed, dict):
                    # Each YAML block should have executor_type as the top-level key
                    for executor_type, config in parsed.items():
                        if config and isinstance(config, dict):
                            defaults[executor_type] = config
            except yaml.YAMLError as e:
                logger.warning(f"Failed to parse YAML block: {e}")
                continue

        return defaults

    def get_defaults(self, executor_type: str) -> dict[str, Any]:
        """Get default configuration for an executor type.

        Args:
            executor_type: The executor type (e.g., 'position_executor', 'dca_executor')

        Returns:
            Dictionary of default configuration values, or empty dict if none set
        """
        content = self._read_content()
        all_defaults = self._parse_yaml_blocks(content)
        return all_defaults.get(executor_type, {})

    def get_all_defaults(self) -> dict[str, dict[str, Any]]:
        """Get all default configurations.

        Returns:
            Dictionary mapping executor types to their default configurations
        """
        content = self._read_content()
        return self._parse_yaml_blocks(content)

    def update_defaults(self, executor_type: str, config: dict[str, Any]) -> None:
        """Update default configuration for an executor type.

        Merges new config with existing defaults so that only the provided
        keys are updated while previously saved keys are preserved — a partial
        nested block updates that block rather than truncating it.

        Args:
            executor_type: The executor type to update
            config: The configuration keys to update (merged with existing defaults)
        """
        content = self._read_content()

        # Merge with existing defaults so we don't lose previously saved keys
        existing_defaults = self.get_defaults(executor_type)
        merged_config = _merge_one_level(existing_defaults, config)

        # Create the new YAML block
        new_yaml = yaml.dump(
            {executor_type: merged_config}, default_flow_style=False, sort_keys=False
        )
        new_block = f"```yaml\n{new_yaml}```"

        # Pattern to find the existing block for this executor type
        # Look for ```yaml followed by the executor type key
        pattern = rf"```yaml\s*\n{re.escape(executor_type)}:[\s\S]*?```"

        if re.search(pattern, content):
            # Replace existing block
            content = re.sub(pattern, new_block, content)
        else:
            # Append new block before the last "---" separator or at the end
            # Find the appropriate section to add the block
            section_header = f"### {executor_type.replace('_', ' ').title()} Defaults"
            if section_header in content:
                # Find the section and add after the header
                pattern = rf"({re.escape(section_header)}\s*\n\n)```yaml[\s\S]*?```"
                if re.search(pattern, content):
                    content = re.sub(pattern, rf"\1{new_block}", content)
                else:
                    # Section exists but no yaml block, add it
                    content = content.replace(
                        section_header, f"{section_header}\n\n{new_block}"
                    )
            else:
                # No section found, append before the footer
                footer_pattern = r"\n---\s*\n\*Last updated:"
                if re.search(footer_pattern, content):
                    content = re.sub(
                        footer_pattern,
                        f"\n### {executor_type.replace('_', ' ').title()} Defaults\n\n{new_block}\n\n---\n\n*Last updated:",
                        content,
                    )
                else:
                    # Just append at the end
                    content += f"\n\n### {executor_type.replace('_', ' ').title()} Defaults\n\n{new_block}\n"

        # Update the last updated timestamp
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = re.sub(
            r"\*Last updated:.*\*", f"*Last updated: {timestamp}*", content
        )

        self._write_content(content)
        logger.info(f"Updated defaults for {executor_type}")

    def merge_with_defaults(
        self, executor_type: str, user_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge user configuration with stored defaults.

        User-provided values take precedence over defaults, key by key — including
        inside a nested block such as ``triple_barrier_config``, which is merged
        rather than replaced (see :func:`_merge_one_level`).

        Args:
            executor_type: The executor type
            user_config: User-provided configuration

        Returns:
            Merged configuration with defaults filled in
        """
        defaults = self.get_defaults(executor_type)
        return _merge_one_level(defaults, user_config)

    def get_raw_content(self) -> str:
        """Get the raw markdown content of the preferences file.

        Returns:
            The full text content of the preferences file
        """
        return self._read_content()

    def save_content(self, content: str) -> None:
        """Save raw content to the preferences file.

        This replaces the entire file content, allowing the AI to make
        intelligent edits (add notes, organize by exchange, etc.).

        Args:
            content: The complete markdown content to write
        """
        self._write_content(content)
        logger.info("Saved preferences file content")

    def get_preferences_path(self) -> str:
        """Get the path to the preferences file.

        Returns:
            String path to the preferences file
        """
        return str(self.preferences_path)

    def reset_to_defaults(self) -> dict[str, dict[str, Any]]:
        """Reset the preferences file to the default template, preserving user YAML configs.

        Saves all current YAML configurations, writes the new template,
        then re-applies each saved config.

        Returns:
            Dictionary of preserved configs (executor_type -> config dict).
        """
        # Save current YAML configs before resetting
        preserved = self.get_all_defaults()

        # Write the new template
        self._write_template()

        # Re-apply each saved config
        for executor_type, config in preserved.items():
            if config:
                self.update_defaults(executor_type, config)

        logger.info(
            f"Reset executor preferences to defaults, preserved {len(preserved)} config(s)"
        )
        return preserved


# Global instance for convenience
executor_preferences = ExecutorPreferencesManager()

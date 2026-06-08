from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TRADING_AGENTS_DIR = ROOT_DIR / "trading_agents"
REPORTS_DIR = ROOT_DIR / "reports"
REPORTS_INDEX_PATH = REPORTS_DIR / "reports_index.json"

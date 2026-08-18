"""The dashboard's issue reporter prefills GitHub's issue forms by field id.

GitHub silently ignores a query parameter that names no field, so a renamed id
in `.github/ISSUE_TEMPLATE/*.yml` does not break the link — it just drops the
prefill, and nobody notices until a report arrives with empty boxes. The same
goes for the `area` dropdown, which is matched by its label. These tests hold
the two files together.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
ISSUE_TS = REPO_ROOT / "frontend" / "src" / "lib" / "github-issue.ts"
CONTEXT_TS = REPO_ROOT / "frontend" / "src" / "lib" / "page-context.ts"


def _template(name: str) -> dict:
    return yaml.safe_load((TEMPLATES / name).read_text())


def _field_ids(template: dict) -> set[str]:
    return {b["id"] for b in template["body"] if "id" in b}


def _ts_field_map(kind: str) -> dict[str, str]:
    """The `FIELDS[kind]` literal from github-issue.ts, as a dict."""
    source = ISSUE_TS.read_text()
    block = re.search(rf"\b{kind}:\s*\{{(.*?)\}},", source, re.S)
    assert block, f"no FIELDS entry for {kind!r} in github-issue.ts"
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))


@pytest.mark.parametrize("kind", ["bug", "feature"])
def test_prefilled_ids_exist_in_the_template(kind):
    """Every id the frontend fills is a field the template actually declares."""
    mapping = _ts_field_map(kind)
    template = _template(mapping["template"])
    declared = _field_ids(template)

    for slot, field_id in mapping.items():
        if slot == "template":
            continue
        assert field_id in declared, (
            f"github-issue.ts prefills {field_id!r} for a {kind} report, but "
            f"{mapping['template']} declares {sorted(declared)}"
        )


@pytest.mark.parametrize("name", ["bug_report.yml", "feature_request.yml"])
def test_area_options_match_the_frontend(name):
    """The dropdown is matched by label — both files must offer the same list."""
    template = _template(name)
    area = next(b for b in template["body"] if b.get("id") == "area")
    options = area["attributes"]["options"]

    declared = re.search(
        r"export const AREAS = \[(.*?)\] as const;", CONTEXT_TS.read_text(), re.S
    )
    assert declared, "AREAS is no longer a literal array in page-context.ts"
    frontend_areas = re.findall(r'"([^"]+)"', declared.group(1))

    assert options == frontend_areas


def test_templates_are_valid_issue_forms():
    """Name, description and a required body — what GitHub needs to render them."""
    for path in sorted(TEMPLATES.glob("*.yml")):
        data = yaml.safe_load(path.read_text())
        if path.name == "config.yml":
            assert "contact_links" in data
            continue
        assert data["name"] and data["description"]
        assert any(
            b.get("validations", {}).get("required") for b in data["body"]
        ), f"{path.name} asks nothing of the reporter"

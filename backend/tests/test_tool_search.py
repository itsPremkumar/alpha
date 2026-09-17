import json

from agent_workspace.tools.builtins.tool_search_tool import (
    catalog_tool_call,
    catalog_tool_describe,
    catalog_tool_search,
)
from agent_workspace.tools.search.catalog import UniversalToolCatalog, get_universal_catalog


def test_catalog_register_search_describe_call():
    catalog = UniversalToolCatalog()
    catalog.register_tool(
        name="web_scrape",
        handler=lambda url, max_chars=1000: f"Scraped {url} (limit: {max_chars})",
        description="Scrape web pages safely",
        category="network",
        parameters_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to scrape"},
                "max_chars": {"type": "integer", "default": 1000},
            },
            "required": ["url"],
        },
    )
    catalog.register_tool(
        name="git_log",
        handler=lambda count=5: f"Log with {count} commits",
        description="Show recent git commit history",
        category="vcs",
    )

    # 1. Search
    results = catalog.search("scrape")
    assert len(results) == 1
    assert results[0]["name"] == "web_scrape"
    assert results[0]["category"] == "network"

    # Search by category
    cat_results = catalog.search("vcs")
    assert len(cat_results) == 1
    assert cat_results[0]["name"] == "git_log"

    # 2. Describe
    desc = catalog.describe("web_scrape")
    assert desc["name"] == "web_scrape"
    assert "url" in desc["parameters"]["properties"]

    # 3. Call
    output = catalog.call("web_scrape", {"url": "https://example.com", "max_chars": 500})
    assert output == "Scraped https://example.com (limit: 500)"


def test_catalog_tools_builtins():
    catalog = get_universal_catalog()
    catalog.register_tool(
        name="calc_sqrt",
        handler=lambda x: {"sqrt": x**0.5},
        description="Calculate square root of a number",
        category="math",
        parameters_schema={
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
        },
    )

    # Search
    search_out = catalog_tool_search.invoke({"query": "sqrt"})
    assert "calc_sqrt" in search_out
    assert "math" in search_out

    # Describe
    desc_out = catalog_tool_describe.invoke({"tool_name": "calc_sqrt"})
    parsed_desc = json.loads(desc_out)
    assert parsed_desc["name"] == "calc_sqrt"
    assert "x" in parsed_desc["parameters"]["properties"]

    # Call
    call_out = catalog_tool_call.invoke({"tool_name": "calc_sqrt", "arguments": {"x": 16}})
    parsed_call = json.loads(call_out)
    assert parsed_call["sqrt"] == 4.0

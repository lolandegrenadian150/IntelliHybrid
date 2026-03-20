"""
IntelliHybrid — AI Schema Intelligence
Automatically generates human-readable descriptions for DynamoDB tables,
attributes, and data patterns using Claude AI.
"""

import json
import logging
from typing import Dict, List, Optional, Any

from src.aws.dynamodb import DynamoDBManager
from src.core.config_loader import HybridConfig

logger = logging.getLogger(__name__)


class SchemaIntelligence:
    """
    Uses Claude AI to understand and describe DynamoDB table schemas.
    
    Capabilities:
    - Auto-generate descriptions for every attribute/column
    - Infer business purpose from table and key names
    - Detect data types and relationships
    - Generate a full data dictionary
    - Suggest better schema designs
    """

    def __init__(self, config: HybridConfig, anthropic_api_key: str):
        self.config = config
        self.api_key = anthropic_api_key
        self.dynamo = DynamoDBManager(config)
        self._cache: Dict[str, Dict] = {}  # table_name → AI descriptions

    async def describe_table(self, table_name: str, sample_items: Optional[List[Dict]] = None) -> Dict:
        """
        Generate a full AI-powered description of a DynamoDB table.
        
        Returns a dict with:
        - table_description: what this table likely stores
        - attribute_descriptions: {attr_name: description} for every field
        - access_patterns: likely query patterns based on key design
        - suggestions: schema improvement suggestions
        """
        if table_name in self._cache:
            logger.debug(f"Returning cached schema intelligence for '{table_name}'")
            return self._cache[table_name]

        # Get actual table metadata
        table_meta = self.dynamo.describe_table(table_name)

        # Get sample data if not provided
        if not sample_items:
            try:
                all_items = self.dynamo.scan_table(table_name)
                sample_items = all_items[:10]  # max 10 samples for AI
            except Exception:
                sample_items = []

        prompt = self._build_schema_prompt(table_name, table_meta, sample_items)
        ai_response = await self._call_claude(prompt)

        try:
            result = json.loads(ai_response)
        except json.JSONDecodeError:
            # Fallback: wrap raw response
            result = {
                "table_description": ai_response,
                "attribute_descriptions": {},
                "access_patterns": [],
                "suggestions": [],
            }

        self._cache[table_name] = result
        return result

    async def describe_all_tables(self) -> Dict[str, Dict]:
        """Generate AI descriptions for all tables in config."""
        results = {}
        for table_cfg in self.config.dynamodb.tables:
            logger.info(f"Generating AI schema intelligence for '{table_cfg.name}'...")
            results[table_cfg.name] = await self.describe_table(table_cfg.name)
        return results

    async def generate_data_dictionary(self, table_name: str) -> str:
        """
        Generate a formatted markdown data dictionary for a table.
        Suitable for documentation, wikis, or EB-1A evidence.
        """
        description = await self.describe_table(table_name)
        table_meta = self.dynamo.describe_table(table_name)

        lines = [
            f"# Data Dictionary: `{table_name}`\n",
            f"**Purpose:** {description.get('table_description', 'N/A')}\n",
            "\n## Attributes\n",
            "| Attribute | Type | Description |",
            "|-----------|------|-------------|",
        ]

        attr_descs = description.get("attribute_descriptions", {})
        key_schema = {k["AttributeName"]: k["KeyType"] for k in table_meta.get("KeySchema", [])}
        attr_defs = {a["AttributeName"]: a["AttributeType"] for a in table_meta.get("AttributeDefinitions", [])}

        for attr_name, desc in attr_descs.items():
            key_marker = ""
            if attr_name in key_schema:
                key_marker = " 🔑 PK" if key_schema[attr_name] == "HASH" else " 🔑 SK"
            attr_type = attr_defs.get(attr_name, "Any")
            lines.append(f"| `{attr_name}`{key_marker} | {attr_type} | {desc} |")

        if description.get("access_patterns"):
            lines.append("\n## Common Access Patterns\n")
            for i, pattern in enumerate(description["access_patterns"], 1):
                lines.append(f"{i}. {pattern}")

        if description.get("suggestions"):
            lines.append("\n## Schema Suggestions\n")
            for suggestion in description["suggestions"]:
                lines.append(f"- 💡 {suggestion}")

        return "\n".join(lines)

    async def enrich_items(self, table_name: str, items: List[Dict]) -> List[Dict]:
        """
        Add AI-generated '_description' field to each item explaining what it represents.
        Useful for displaying records in human-readable dashboards.
        """
        schema_desc = await self.describe_table(table_name)
        attr_descs = schema_desc.get("attribute_descriptions", {})

        enriched = []
        for item in items:
            enriched_item = dict(item)
            # Add a human-readable summary of this record
            summary_parts = []
            for key, value in item.items():
                if key in attr_descs:
                    summary_parts.append(f"{attr_descs[key]}: {value}")
            enriched_item["_ai_summary"] = " | ".join(summary_parts[:4])  # top 4 fields
            enriched.append(enriched_item)

        return enriched

    # ------------------------------------------------------------------ #
    #  Prompt Building
    # ------------------------------------------------------------------ #

    def _build_schema_prompt(
        self,
        table_name: str,
        table_meta: Dict,
        sample_items: List[Dict],
    ) -> str:
        key_schema = table_meta.get("KeySchema", [])
        attr_defs = table_meta.get("AttributeDefinitions", [])
        gsi_list = table_meta.get("GlobalSecondaryIndexes", [])

        sample_json = json.dumps(sample_items[:5], default=str, indent=2) if sample_items else "No sample data available"

        return f"""You are a data architect analyzing a DynamoDB table. 
Based on the table name, key schema, and sample data, generate intelligent descriptions.

TABLE NAME: {table_name}

KEY SCHEMA:
{json.dumps(key_schema, indent=2)}

ATTRIBUTE DEFINITIONS:
{json.dumps(attr_defs, indent=2)}

GLOBAL SECONDARY INDEXES:
{json.dumps(gsi_list, indent=2) if gsi_list else "None"}

SAMPLE DATA (up to 5 items):
{sample_json}

Respond ONLY with a valid JSON object (no markdown, no backticks) in this exact format:
{{
  "table_description": "One sentence describing what business entity or concept this table stores",
  "attribute_descriptions": {{
    "attributeName": "Clear, business-friendly description of what this attribute represents",
    "anotherAttribute": "Description..."
  }},
  "access_patterns": [
    "Description of a likely query pattern this table supports",
    "Another access pattern..."
  ],
  "suggestions": [
    "Optional: schema improvement suggestion if applicable"
  ]
}}

Be specific and business-focused. Infer domain context from naming conventions.
For attribute_descriptions, include ALL attributes visible in the sample data, not just the key schema ones."""

    # ------------------------------------------------------------------ #
    #  Claude API Call
    # ------------------------------------------------------------------ #

    async def _call_claude(self, prompt: str) -> str:
        """Call Claude API and return the text response."""
        import aiohttp
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            ) as resp:
                data = await resp.json()
                return data["content"][0]["text"]

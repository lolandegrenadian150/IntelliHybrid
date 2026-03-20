"""
IntelliHybrid — AI Natural Language Query Engine
Write queries in plain English. IntelliHybrid translates them to
DynamoDB operations and executes them — no PartiQL or SDK knowledge needed.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Key, Attr

from src.aws.dynamodb import DynamoDBManager
from src.core.config_loader import HybridConfig

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    """Result of a natural language query."""
    natural_language: str          # original user question
    interpreted_as: str            # what the AI understood
    dynamo_operation: str          # get_item | query | scan | put_item | delete_item
    dynamo_params: Dict            # the actual params sent to DynamoDB
    results: List[Dict]            # returned items
    count: int                     # number of items
    explanation: str               # plain English explanation of what was done
    error: Optional[str] = None    # if something went wrong


class NaturalLanguageQueryEngine:
    """
    Translates natural language questions into DynamoDB operations.

    Examples:
        "Show me all orders from customer C-001"
        "Find the user with email alice@example.com"
        "How many products are in the electronics category?"
        "Get all sessions that expired before yesterday"
        "Show me the 5 most recent orders"
        "Delete the product with ID prod-999"
        "Add a new user: John Doe, email john@example.com"
    """

    def __init__(self, config: HybridConfig, anthropic_api_key: str):
        self.config = config
        self.api_key = anthropic_api_key
        self.dynamo = DynamoDBManager(config)
        self._schema_cache: Dict[str, Dict] = {}
        self._history: List[Dict] = []   # conversation history for follow-up queries

    async def ask(self, question: str, table_name: Optional[str] = None) -> QueryResult:
        """
        Execute a natural language query against DynamoDB.

        Args:
            question: plain English question or command
            table_name: optional — if not given, AI infers the table from the question

        Returns:
            QueryResult with data and plain English explanation
        """
        logger.info(f"NL Query: '{question}'")

        # Load schema context
        schema_context = await self._get_schema_context(table_name)

        # Ask Claude to translate the question
        translation = await self._translate_to_dynamo(question, schema_context)

        # Execute the translated operation
        result = await self._execute(translation, question)

        # Add to history for follow-up context
        self._history.append({
            "question": question,
            "table": translation.get("table_name"),
            "operation": translation.get("operation"),
        })

        return result

    async def ask_streaming(self, question: str, table_name: Optional[str] = None):
        """
        Generator version of ask() that yields status updates as it works.
        Useful for showing progress in a UI.
        """
        yield {"status": "thinking", "message": "Understanding your question..."}
        schema_context = await self._get_schema_context(table_name)

        yield {"status": "translating", "message": "Translating to DynamoDB operation..."}
        translation = await self._translate_to_dynamo(question, schema_context)

        yield {"status": "executing", "message": f"Running {translation.get('operation', 'query')} on {translation.get('table_name', 'table')}..."}
        result = await self._execute(translation, question)

        yield {"status": "done", "result": result}

    async def explain_table(self, table_name: str) -> str:
        """Ask the AI to explain what a table does in plain English."""
        schema = await self._get_schema_context(table_name)
        prompt = f"""You are a helpful data assistant. In 2-3 sentences, explain to a non-technical user 
what the following DynamoDB table stores and what it's used for.

Table context:
{json.dumps(schema, indent=2)}

Respond in plain English only, no technical jargon."""
        return await self._call_claude(prompt)

    async def suggest_queries(self, table_name: str) -> List[str]:
        """Return a list of example natural language queries for a table."""
        schema = await self._get_schema_context(table_name)
        prompt = f"""Given this DynamoDB table schema, generate 8 useful example questions 
a business user might ask about this data. Write them as natural language questions.

Table schema:
{json.dumps(schema, indent=2)}

Respond ONLY with a JSON array of strings. No markdown, no explanation.
Example: ["Show me all users", "Find order O-001", ...]"""

        response = await self._call_claude(prompt)
        try:
            return json.loads(response)
        except Exception:
            return ["Show me all records", f"Find items in {table_name}"]

    # ------------------------------------------------------------------ #
    #  Translation
    # ------------------------------------------------------------------ #

    async def _translate_to_dynamo(self, question: str, schema_context: Dict) -> Dict:
        """Use Claude to translate a natural language question to a DynamoDB operation plan."""
        history_context = ""
        if self._history:
            last = self._history[-3:]  # last 3 queries for context
            history_context = f"\nRecent query history:\n{json.dumps(last, indent=2)}"

        prompt = f"""You are a DynamoDB query translator. Convert natural language questions 
into DynamoDB operations.

AVAILABLE TABLES AND SCHEMAS:
{json.dumps(schema_context, indent=2)}
{history_context}

USER QUESTION: "{question}"

Respond ONLY with a valid JSON object (no markdown, no backticks):
{{
  "table_name": "the DynamoDB table to query",
  "operation": "scan | query | get_item | put_item | delete_item | update_item",
  "params": {{
    // For scan: {{ "FilterExpression": "attribute = :val", "ExpressionAttributeValues": {{":val": {{"S": "value"}}}} }}
    // For query: {{ "KeyConditionExpression": "pk = :pk", "ExpressionAttributeValues": {{":pk": {{"S": "value"}}}} }}
    // For get_item: {{ "Key": {{"pk": {{"S": "value"}}}} }}
    // For put_item: {{ "Item": {{...}} }}
    // For delete_item: {{ "Key": {{"pk": {{"S": "value"}}}} }}
  }},
  "interpreted_as": "One sentence: what you understood the user to be asking",
  "explanation": "One sentence: what DynamoDB operation you will run and why"
}}

Rules:
- Choose the most efficient operation (prefer query over scan when possible)
- Use the correct DynamoDB type syntax: S for String, N for Number, BOOL for boolean
- If the question is ambiguous, choose the most likely interpretation
- If no matching table exists, use the closest one"""

        response = await self._call_claude(prompt)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.warning(f"Could not parse Claude translation: {response}")
            # fallback to a scan
            table_name = list(schema_context.keys())[0] if schema_context else "unknown"
            return {
                "table_name": table_name,
                "operation": "scan",
                "params": {},
                "interpreted_as": question,
                "explanation": f"Running a full scan on {table_name}",
            }

    # ------------------------------------------------------------------ #
    #  Execution
    # ------------------------------------------------------------------ #

    async def _execute(self, translation: Dict, original_question: str) -> QueryResult:
        """Execute the translated DynamoDB operation."""
        table_name = translation.get("table_name", "")
        operation = translation.get("operation", "scan")
        params = translation.get("params", {})
        interpreted_as = translation.get("interpreted_as", original_question)
        explanation = translation.get("explanation", "")

        try:
            items = []
            if operation == "scan":
                items = self._execute_scan(table_name, params)
            elif operation == "query":
                items = self._execute_query(table_name, params)
            elif operation == "get_item":
                item = self.dynamo.get_item(table_name, params.get("Key", {}))
                items = [item] if item else []
            elif operation == "put_item":
                self.dynamo.put_item(table_name, params.get("Item", {}))
                items = [params.get("Item", {})]
                explanation = f"Successfully inserted/updated the record in {table_name}."
            elif operation == "delete_item":
                table = self.dynamo.resource.Table(table_name)
                table.delete_item(Key=params.get("Key", {}))
                items = []
                explanation = f"Record deleted from {table_name}."
            elif operation == "update_item":
                table = self.dynamo.resource.Table(table_name)
                table.update_item(**params)
                items = []
                explanation = f"Record updated in {table_name}."

            return QueryResult(
                natural_language=original_question,
                interpreted_as=interpreted_as,
                dynamo_operation=operation,
                dynamo_params=params,
                results=items,
                count=len(items),
                explanation=explanation,
            )

        except Exception as e:
            logger.error(f"Query execution error: {e}", exc_info=True)
            return QueryResult(
                natural_language=original_question,
                interpreted_as=interpreted_as,
                dynamo_operation=operation,
                dynamo_params=params,
                results=[],
                count=0,
                explanation=explanation,
                error=str(e),
            )

    def _execute_scan(self, table_name: str, params: Dict) -> List[Dict]:
        """Execute a DynamoDB scan, handling FilterExpression strings."""
        table = self.dynamo.resource.Table(table_name)
        scan_kwargs = {}

        if "FilterExpression" in params:
            from boto3.dynamodb.conditions import Attr
            # Parse simple expressions like "category = :cat"
            fe_str = params["FilterExpression"]
            ea_vals = params.get("ExpressionAttributeValues", {})
            # Convert DynamoDB typed values to plain Python
            plain_vals = {k: list(v.values())[0] for k, v in ea_vals.items()}
            scan_kwargs["FilterExpression"] = fe_str
            scan_kwargs["ExpressionAttributeValues"] = plain_vals

        if "Limit" in params:
            scan_kwargs["Limit"] = params["Limit"]

        items = []
        while True:
            response = table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key or ("Limit" in params and len(items) >= params["Limit"]):
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        return items

    def _execute_query(self, table_name: str, params: Dict) -> List[Dict]:
        """Execute a DynamoDB query."""
        table = self.dynamo.resource.Table(table_name)
        query_kwargs = {}

        if "KeyConditionExpression" in params:
            query_kwargs["KeyConditionExpression"] = params["KeyConditionExpression"]

        if "ExpressionAttributeValues" in params:
            ea_vals = params["ExpressionAttributeValues"]
            plain_vals = {k: list(v.values())[0] for k, v in ea_vals.items()}
            query_kwargs["ExpressionAttributeValues"] = plain_vals

        if "IndexName" in params:
            query_kwargs["IndexName"] = params["IndexName"]

        response = table.query(**query_kwargs)
        return response.get("Items", [])

    # ------------------------------------------------------------------ #
    #  Schema Context
    # ------------------------------------------------------------------ #

    async def _get_schema_context(self, table_name: Optional[str] = None) -> Dict:
        """Build schema context for Claude to understand the available tables."""
        if table_name:
            tables_to_describe = [table_name]
        else:
            tables_to_describe = [t.name for t in self.config.dynamodb.tables]

        context = {}
        for name in tables_to_describe:
            if name not in self._schema_cache:
                try:
                    meta = self.dynamo.describe_table(name)
                    sample = self.dynamo.scan_table(name)[:3]
                    self._schema_cache[name] = {
                        "key_schema": meta.get("KeySchema", []),
                        "attribute_definitions": meta.get("AttributeDefinitions", []),
                        "gsi": [g["IndexName"] for g in meta.get("GlobalSecondaryIndexes", [])],
                        "sample_item_keys": list(sample[0].keys()) if sample else [],
                    }
                except Exception as e:
                    logger.warning(f"Could not load schema for '{name}': {e}")
                    self._schema_cache[name] = {}
            context[name] = self._schema_cache[name]

        return context

    # ------------------------------------------------------------------ #
    #  Claude API
    # ------------------------------------------------------------------ #

    async def _call_claude(self, prompt: str) -> str:
        import aiohttp
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2000,
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

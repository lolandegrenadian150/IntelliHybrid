"""
IntelliHybrid — AI Assistant
The main AI-powered interface for IntelliHybrid.
Combines natural language queries, schema intelligence, and conversational help.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional

from src.ai.query_engine import NaturalLanguageQueryEngine, QueryResult
from src.ai.schema_intelligence import SchemaIntelligence
from src.aws.dynamodb import DynamoDBManager
from src.core.config_loader import HybridConfig

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    role: str    # "user" | "assistant"
    content: str
    data: Optional[Dict] = None      # structured data (query results, etc.)


@dataclass
class ChatSession:
    messages: List[ChatMessage] = field(default_factory=list)
    active_table: Optional[str] = None

    def add(self, role: str, content: str, data: Optional[Dict] = None):
        self.messages.append(ChatMessage(role=role, content=content, data=data))

    def history_for_api(self) -> List[Dict]:
        """Format messages for Anthropic API."""
        return [
            {"role": m.role, "content": m.content}
            for m in self.messages[-10:]   # last 10 turns for context
        ]


class AIAssistant:
    """
    Conversational AI assistant for IntelliHybrid.

    Users can:
    - Ask questions in plain English and get data back
    - Ask for table/column descriptions
    - Get a full data dictionary generated automatically
    - Have a multi-turn conversation about their data

    Usage:
        assistant = AIAssistant(config, api_key="sk-ant-...")
        result = await assistant.chat("Show me all orders from customer C-001")
        print(result.content)
        print(result.data)  # the actual DynamoDB items
    """

    SYSTEM_PROMPT = """You are IntelliHybrid's AI assistant — a friendly, expert data analyst 
specializing in hybrid cloud architectures and DynamoDB.

You help users:
1. Query their DynamoDB tables using natural language
2. Understand what their tables and columns mean
3. Get insights from their data
4. Troubleshoot hybrid cloud connectivity issues

When a user asks a data question, you execute it against their real DynamoDB tables 
and explain the results clearly.

Always be concise, accurate, and business-focused. 
When showing data results, summarize key findings rather than just listing raw data."""

    def __init__(self, config: HybridConfig, anthropic_api_key: Optional[str] = None):
        self.config = config
        self.api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY env var "
                "or pass anthropic_api_key to AIAssistant()."
            )
        self.query_engine = NaturalLanguageQueryEngine(config, self.api_key)
        self.schema_intel = SchemaIntelligence(config, self.api_key)
        self.dynamo = DynamoDBManager(config)
        self._session = ChatSession()

    async def chat(self, user_message: str) -> ChatMessage:
        """
        Process a user message and return the assistant's response.
        Automatically detects intent (query, schema question, general help).
        """
        self._session.add("user", user_message)

        intent = await self._detect_intent(user_message)
        logger.info(f"Detected intent: {intent}")

        if intent == "data_query":
            response = await self._handle_data_query(user_message)
        elif intent == "schema_question":
            response = await self._handle_schema_question(user_message)
        elif intent == "data_dictionary":
            response = await self._handle_data_dictionary(user_message)
        elif intent == "table_list":
            response = await self._handle_table_list()
        elif intent == "suggest_queries":
            response = await self._handle_suggest_queries(user_message)
        else:
            response = await self._handle_general(user_message)

        self._session.add("assistant", response.content, response.data)
        return response

    async def chat_stream(self, user_message: str) -> AsyncGenerator[str, None]:
        """Streaming version — yields tokens as they arrive from Claude."""
        self._session.add("user", user_message)
        import aiohttp

        messages = self._session.history_for_api()
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1500,
            "system": self.SYSTEM_PROMPT,
            "messages": messages,
            "stream": True,
        }
        full_response = ""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            ) as resp:
                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data:"):
                        try:
                            data = json.loads(line[5:])
                            if data.get("type") == "content_block_delta":
                                token = data["delta"].get("text", "")
                                full_response += token
                                yield token
                        except Exception:
                            pass

        self._session.add("assistant", full_response)

    # ------------------------------------------------------------------ #
    #  Intent Handlers
    # ------------------------------------------------------------------ #

    async def _handle_data_query(self, question: str) -> ChatMessage:
        """Execute a data query and format the response."""
        result: QueryResult = await self.query_engine.ask(question, self._session.active_table)

        if result.error:
            content = (
                f"I tried to run that query but hit an error: `{result.error}`\n\n"
                f"I interpreted your question as: *{result.interpreted_as}*\n\n"
                "Could you rephrase, or let me know which table you meant?"
            )
            return ChatMessage(role="assistant", content=content)

        if result.count == 0:
            content = (
                f"I ran the query and found **no matching records**.\n\n"
                f"*What I looked for:* {result.interpreted_as}\n"
                f"*Operation:* `{result.dynamo_operation}` on `{result.dynamo_params.get('TableName', 'the table')}`"
            )
        elif result.count == 1:
            content = (
                f"Found **1 record**. {result.explanation}\n\n"
                f"```json\n{json.dumps(result.results[0], default=str, indent=2)}\n```"
            )
        else:
            # Summarize multiple results
            content = (
                f"Found **{result.count} records**. {result.explanation}\n\n"
                f"Here's a preview of the first 3:\n"
                f"```json\n{json.dumps(result.results[:3], default=str, indent=2)}\n```"
            )
            if result.count > 3:
                content += f"\n\n*...and {result.count - 3} more. Ask me to filter or narrow down the results.*"

        return ChatMessage(
            role="assistant",
            content=content,
            data={
                "items": result.results,
                "count": result.count,
                "operation": result.dynamo_operation,
                "interpreted_as": result.interpreted_as,
            },
        )

    async def _handle_schema_question(self, question: str) -> ChatMessage:
        """Answer questions about table structure and column meanings."""
        # Infer table from question or use active
        table_name = self._infer_table_from_question(question)
        desc = await self.schema_intel.describe_table(table_name)

        content = (
            f"**{table_name}** — {desc.get('table_description', '')}\n\n"
            "**Attribute descriptions:**\n"
        )
        for attr, attr_desc in desc.get("attribute_descriptions", {}).items():
            content += f"- **`{attr}`**: {attr_desc}\n"

        if desc.get("access_patterns"):
            content += "\n**Common query patterns:**\n"
            for p in desc["access_patterns"]:
                content += f"- {p}\n"

        return ChatMessage(role="assistant", content=content, data=desc)

    async def _handle_data_dictionary(self, question: str) -> ChatMessage:
        """Generate and return a data dictionary."""
        table_name = self._infer_table_from_question(question)
        dictionary = await self.schema_intel.generate_data_dictionary(table_name)
        return ChatMessage(
            role="assistant",
            content=f"Here's the AI-generated data dictionary for `{table_name}`:\n\n{dictionary}",
            data={"data_dictionary": dictionary, "table": table_name},
        )

    async def _handle_table_list(self) -> ChatMessage:
        """List all available tables with AI descriptions."""
        tables = self.dynamo.list_tables()
        content = f"You have **{len(tables)} DynamoDB tables**:\n\n"
        for t in tables:
            try:
                explanation = await self.query_engine.explain_table(t)
                content += f"- **`{t}`** — {explanation}\n"
            except Exception:
                content += f"- **`{t}`**\n"
        content += "\nAsk me anything about any of these tables!"
        return ChatMessage(role="assistant", content=content, data={"tables": tables})

    async def _handle_suggest_queries(self, question: str) -> ChatMessage:
        """Suggest example queries for a table."""
        table_name = self._infer_table_from_question(question)
        suggestions = await self.query_engine.suggest_queries(table_name)
        content = f"Here are some things you can ask about **`{table_name}`**:\n\n"
        for i, s in enumerate(suggestions, 1):
            content += f"{i}. {s}\n"
        return ChatMessage(role="assistant", content=content, data={"suggestions": suggestions})

    async def _handle_general(self, message: str) -> ChatMessage:
        """Handle general questions using Claude with IntelliHybrid context."""
        messages = self._session.history_for_api()
        response_text = await self._call_claude_chat(messages)
        return ChatMessage(role="assistant", content=response_text)

    # ------------------------------------------------------------------ #
    #  Intent Detection
    # ------------------------------------------------------------------ #

    async def _detect_intent(self, message: str) -> str:
        """Classify the user's intent to route to the right handler."""
        lower = message.lower()
        data_query_keywords = [
            "show", "find", "get", "fetch", "list", "search", "how many",
            "count", "give me", "what are", "which", "delete", "remove",
            "add", "insert", "create a record", "update", "set",
        ]
        schema_keywords = ["what is", "what does", "describe", "explain", "meaning of", "what's", "column", "attribute", "field"]
        dict_keywords = ["data dictionary", "document", "documentation", "all columns", "all attributes", "all fields"]
        table_list_keywords = ["what tables", "which tables", "list tables", "available tables", "all tables"]
        suggest_keywords = ["suggest", "example", "what can i ask", "ideas", "what queries"]

        if any(k in lower for k in dict_keywords):
            return "data_dictionary"
        if any(k in lower for k in table_list_keywords):
            return "table_list"
        if any(k in lower for k in suggest_keywords):
            return "suggest_queries"
        if any(k in lower for k in schema_keywords):
            return "schema_question"
        if any(k in lower for k in data_query_keywords):
            return "data_query"
        return "general"

    def _infer_table_from_question(self, question: str) -> str:
        """Try to extract a table name from the question, else use active or first."""
        lower = question.lower()
        for table_cfg in self.config.dynamodb.tables:
            if table_cfg.name.lower() in lower or table_cfg.name.replace("-", " ").lower() in lower:
                return table_cfg.name
        if self._session.active_table:
            return self._session.active_table
        if self.config.dynamodb.tables:
            return self.config.dynamodb.tables[0].name
        return "unknown-table"

    # ------------------------------------------------------------------ #
    #  Claude API
    # ------------------------------------------------------------------ #

    async def _call_claude_chat(self, messages: List[Dict]) -> str:
        import aiohttp
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system": self.SYSTEM_PROMPT,
            "messages": messages,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            ) as resp:
                data = await resp.json()
                return data["content"][0]["text"]

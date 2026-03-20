
---

## 🤖 AI Features (v1.1.0+)

IntelliHybrid includes an AI layer powered by Claude that understands your data — so you don't have to write DynamoDB queries.

### Natural Language Queries

Ask questions in plain English. IntelliHybrid translates them to the correct DynamoDB operation and returns real data.

```python
from intellihybrid.ai import AIAssistant

assistant = AIAssistant(config, anthropic_api_key="sk-ant-...")

# Ask anything
result = await assistant.chat("Show me all orders from customer C-001")
result = await assistant.chat("How many products have stock below 10?")
result = await assistant.chat("Find users who signed up this month")
result = await assistant.chat("Delete the session with ID sess-expired-99")
```

**Example queries that just work:**
- `"Show me all orders from customer C-001"`
- `"How many products are in the electronics category?"`
- `"Find the 5 most expensive items in inventory"`
- `"Get all sessions that expired before yesterday"`
- `"Add a new user: Jane Doe, email jane@example.com"`

---

### AI-Generated Column Descriptions

IntelliHybrid reads your table schema and sample data, then uses AI to write human-readable descriptions for every attribute — automatically.

```python
from intellihybrid.ai import SchemaIntelligence

intel = SchemaIntelligence(config, anthropic_api_key="sk-ant-...")

# Get AI descriptions for every column
description = await intel.describe_table("orders-table")
# Returns:
# {
#   "table_description": "Stores customer order transactions with fulfillment status tracking",
#   "attribute_descriptions": {
#     "orderId": "Unique identifier for each order transaction",
#     "customerId": "References the customer — links to users-table partition key",
#     "status": "Current fulfillment state: processing, shipped, delivered, or cancelled",
#     "total": "Order value in USD cents",
#     "createdAt": "Unix timestamp when the order was placed, used for date-range queries"
#   },
#   "access_patterns": ["Query all orders for a customer", "Get order by ID"],
#   "suggestions": ["Consider adding a GSI on status for fulfillment dashboards"]
# }
```

---

### Auto-Generated Data Dictionary

One call produces a fully documented markdown data dictionary — ready for wikis, compliance docs, or EB-1A evidence of original work.

```python
dictionary = await intel.generate_data_dictionary("orders-table")
print(dictionary)
# Outputs a formatted markdown table with every attribute described in plain English
```

---

### REST API (for any frontend)

```bash
# Start the AI API server
pip install intellihybrid[ai]
export ANTHROPIC_API_KEY="sk-ant-..."
uvicorn src.ai.server:app --host 0.0.0.0 --port 8080
```

```bash
# Chat endpoint
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me all orders from customer C-001"}'

# Get AI schema description
curl http://localhost:8080/tables/orders-table

# Get full data dictionary
curl http://localhost:8080/tables/orders-table/dictionary

# Get suggested queries
curl http://localhost:8080/tables/orders-table/suggestions
```

Interactive API docs available at `http://localhost:8080/docs`.

---

### AI CLI Commands

```bash
# Ask a question directly from the terminal
intellihybrid ai ask "Show me all low-stock products"

# Describe a table's schema in plain English
intellihybrid ai describe --table orders-table

# Generate a data dictionary
intellihybrid ai dictionary --table orders-table --output docs/orders-dictionary.md

# Get example queries for a table
intellihybrid ai suggest --table orders-table
```

---

### Install with AI Support

```bash
pip install intellihybrid[ai]

# Or from source:
pip install -e ".[ai]"
```

The `[ai]` extra adds `aiohttp` and `fastapi` for the AI API server.
Set your Anthropic API key: `export ANTHROPIC_API_KEY="sk-ant-..."`

---

"""
IntelliHybrid — AI REST API Server
Exposes the AI Assistant over HTTP so any frontend or tool can use it.

Start with:
    uvicorn src.ai.server:app --host 0.0.0.0 --port 8080 --reload

Endpoints:
    POST /chat              — Send a message, get a response
    GET  /tables            — List tables with AI descriptions
    GET  /tables/{name}     — Get AI schema description for a table
    GET  /tables/{name}/dictionary  — Get full data dictionary
    GET  /tables/{name}/suggestions — Get example queries
    POST /query             — Run a natural language query directly
"""

import os
import logging
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.core.config_loader import ConfigLoader
from src.ai.assistant import AIAssistant, ChatSession

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  App Lifecycle
# ------------------------------------------------------------------ #

assistant: Optional[AIAssistant] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global assistant
    config_path = os.environ.get("INTELLIHYBRID_CONFIG", "config/config.yaml")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    try:
        config = ConfigLoader(config_path).load()
        assistant = AIAssistant(config, api_key)
        logger.info("✅ IntelliHybrid AI Assistant initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize AI assistant: {e}")
    yield


app = FastAPI(
    title="IntelliHybrid AI API",
    description="Natural language interface for your on-prem ↔ AWS DynamoDB data",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ #
#  Request/Response Models
# ------------------------------------------------------------------ #

class ChatRequest(BaseModel):
    message: str
    table_name: Optional[str] = None
    stream: bool = False


class ChatResponse(BaseModel):
    role: str = "assistant"
    content: str
    data: Optional[dict] = None


class QueryRequest(BaseModel):
    question: str
    table_name: Optional[str] = None


# ------------------------------------------------------------------ #
#  Routes
# ------------------------------------------------------------------ #

def _check_assistant():
    if not assistant:
        raise HTTPException(status_code=503, detail="AI Assistant not initialized. Check server logs.")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "assistant_ready": assistant is not None,
        "version": "1.0.0",
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a natural language message and get an AI-powered response.
    
    Examples:
    - "Show me all orders from customer C-001"
    - "What does the userId column mean?"
    - "How many products are in the electronics category?"
    - "Generate a data dictionary for orders-table"
    """
    _check_assistant()

    if request.stream:
        async def event_stream():
            async for token in assistant.chat_stream(request.message):
                yield token

        return StreamingResponse(event_stream(), media_type="text/plain")

    response = await assistant.chat(request.message)
    return ChatResponse(role=response.role, content=response.content, data=response.data)


@app.get("/tables")
async def list_tables():
    """List all DynamoDB tables with AI-generated descriptions."""
    _check_assistant()
    tables = assistant.dynamo.list_tables()
    result = {}
    for t in tables:
        try:
            result[t] = await assistant.query_engine.explain_table(t)
        except Exception as e:
            result[t] = f"Could not describe: {e}"
    return {"tables": result}


@app.get("/tables/{table_name}")
async def describe_table(table_name: str):
    """Get AI-generated schema intelligence for a specific table."""
    _check_assistant()
    try:
        description = await assistant.schema_intel.describe_table(table_name)
        return description
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/tables/{table_name}/dictionary")
async def data_dictionary(table_name: str):
    """Generate a full AI-powered data dictionary for a table (markdown format)."""
    _check_assistant()
    try:
        dictionary = await assistant.schema_intel.generate_data_dictionary(table_name)
        return {"table": table_name, "dictionary": dictionary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tables/{table_name}/suggestions")
async def query_suggestions(table_name: str):
    """Get AI-suggested natural language queries for a table."""
    _check_assistant()
    suggestions = await assistant.query_engine.suggest_queries(table_name)
    return {"table": table_name, "suggestions": suggestions}


@app.post("/query")
async def run_query(request: QueryRequest):
    """Run a natural language query directly and get structured results."""
    _check_assistant()
    result = await assistant.query_engine.ask(request.question, request.table_name)
    return {
        "natural_language": result.natural_language,
        "interpreted_as": result.interpreted_as,
        "operation": result.dynamo_operation,
        "count": result.count,
        "results": result.results,
        "explanation": result.explanation,
        "error": result.error,
    }


@app.get("/")
async def root():
    return {
        "name": "IntelliHybrid AI API",
        "docs": "/docs",
        "health": "/health",
        "github": "https://github.com/Clever-Boy/IntelliHybrid",
    }

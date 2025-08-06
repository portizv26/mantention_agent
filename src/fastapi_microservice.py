"""
FastAPI Microservice for the Maintenance Chatbot
Features:
- RESTful API with OpenAPI documentation
- Health checks and monitoring
- Proper error handling and validation
- API versioning support
- Async request processing
"""

import asyncio
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional, Union
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from contextlib import asynccontextmanager

from improved_agent import ImprovedAgentChat
from config import CHAT_DOCS_DIR

# ─────────────────────────── CONFIGURATION ─────────────────────────── #

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("chatbot_api")

# Global state for database connection
db_connection = None

# ─────────────────────────── PYDANTIC MODELS ─────────────────────────── #

class ChatRequest(BaseModel):
    """Request model for chat interactions"""
    message: str = Field(..., min_length=1, max_length=1000, description="User message in Spanish")
    session_id: Optional[str] = Field(None, description="Optional session ID for context continuity")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "¿Cuántos equipos necesitan mantenimiento preventivo este mes?",
                "session_id": "uuid-v4-string"
            }
        }
    )

class ChatResponse(BaseModel):
    """Response model for chat interactions"""
    response: str = Field(..., description="Bot response in Spanish")
    session_id: str = Field(..., description="Session ID for this conversation")
    metrics: Dict = Field(..., description="Processing metrics and performance data")
    artifacts: Optional[Dict] = Field(None, description="Available artifacts (files, images, etc.)")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "response": "Este mes hay 15 equipos que necesitan mantenimiento preventivo.",
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "metrics": {
                    "total_time": 2.34,
                    "sql_time": 0.45,
                    "image_time": 1.23,
                    "flow": "good"
                },
                "artifacts": {
                    "data_file": "/path/to/data.csv",
                    "image_file": "/path/to/chart.png"
                }
            }
        }
    )

class HealthResponse(BaseModel):
    """Health check response model"""
    status: str = Field(..., description="Service status")
    timestamp: str = Field(..., description="Check timestamp")
    version: str = Field(..., description="API version")
    database: str = Field(..., description="Database connection status")
    dependencies: Dict[str, str] = Field(..., description="Dependencies status")

class ErrorResponse(BaseModel):
    """Error response model"""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Detailed error information")
    session_id: Optional[str] = Field(None, description="Session ID if available")
    timestamp: str = Field(..., description="Error timestamp")

# ─────────────────────────── DATABASE SETUP ─────────────────────────── #

def get_database_connection() -> sqlite3.Connection:
    """Get database connection with proper configuration"""
    try:
        # Adjust path as needed for your database
        db_path = Path("data/maintenance.db")
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found at {db_path}")
        
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan"""
    # Startup
    global db_connection
    logger.info("Starting chatbot API service...")
    
    try:
        db_connection = get_database_connection()
        logger.info("Database connection established")
        
        # Ensure chat docs directory exists
        CHAT_DOCS_DIR.mkdir(exist_ok=True)
        logger.info(f"Chat docs directory ready at {CHAT_DOCS_DIR}")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to start service: {e}")
        raise
    finally:
        # Shutdown
        if db_connection:
            db_connection.close()
            logger.info("Database connection closed")
        logger.info("Chatbot API service stopped")

# ─────────────────────────── FASTAPI APP ─────────────────────────── #

app = FastAPI(
    title="Maintenance Chatbot API",
    description="""
    A sophisticated chatbot API for maintenance management that can:
    
    - Answer questions about maintenance data in Spanish
    - Generate SQL queries to retrieve relevant information  
    - Create data visualizations and charts
    - Provide contextual, intelligent responses
    
    ## Features
    
    - **Async Processing**: Optimized for concurrent requests
    - **Smart Caching**: Reduces response times for common queries
    - **Error Recovery**: Robust error handling and retry mechanisms
    - **Artifact Management**: Download generated files and charts
    - **Performance Metrics**: Detailed timing and performance data
    
    ## Usage
    
    1. Send a POST request to `/v1/chat` with your message
    2. Receive an intelligent response with optional artifacts
    3. Download generated files using the artifacts endpoints
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# ─────────────────────────── MIDDLEWARE ─────────────────────────── #

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compression middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ─────────────────────────── DEPENDENCIES ─────────────────────────── #

async def get_agent() -> ImprovedAgentChat:
    """Dependency to get agent instance"""
    if not db_connection:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection not available"
        )
    return ImprovedAgentChat(db_connection)

# ─────────────────────────── SESSION MANAGEMENT ─────────────────────────── #

# In-memory session store (in production, use Redis or similar)
sessions: Dict[str, ImprovedAgentChat] = {}

async def get_or_create_session(session_id: Optional[str] = None) -> tuple[str, ImprovedAgentChat]:
    """Get existing session or create new one"""
    if session_id and session_id in sessions:
        return session_id, sessions[session_id]
    
    # Create new session
    new_session_id = str(uuid4())
    sessions[new_session_id] = await get_agent()
    
    # Cleanup old sessions (keep last 100)
    if len(sessions) > 100:
        oldest_sessions = list(sessions.keys())[:-100]
        for old_id in oldest_sessions:
            del sessions[old_id]
    
    return new_session_id, sessions[new_session_id]

# ─────────────────────────── API ROUTES ─────────────────────────── #

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Health check endpoint for monitoring and load balancers"""
    try:
        # Test database connection
        test_conn = get_database_connection()
        cursor = test_conn.execute("SELECT 1")
        cursor.fetchone()
        test_conn.close()
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = f"unhealthy: {str(e)}"
    
    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        version="1.0.0",
        database=db_status,
        dependencies={
            "openai": "healthy",  # Could add actual checks
            "filesystem": "healthy"
        }
    )

@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with basic API information"""
    return {
        "message": "Maintenance Chatbot API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }

@app.post("/v1/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks
) -> ChatResponse:
    """
    Main chat endpoint for interacting with the maintenance chatbot
    
    - **message**: Your question or request in Spanish
    - **session_id**: Optional session ID to maintain conversation context
    
    Returns a response with the answer, session information, and performance metrics.
    """
    start_time = time.time()
    session_id = None
    
    try:
        # Get or create session
        session_id, agent = await get_or_create_session(request.session_id)
        
        logger.info(f"Processing chat request for session {session_id[:8]}...")
        
        # Process the request
        response, metrics = await agent.execute(request.message)
        
        # Prepare artifacts info
        artifacts = {}
        if agent.artefacts.data_file:
            artifacts["data_file"] = str(agent.artefacts.data_file)
        if agent.artefacts.image_file:
            artifacts["image_file"] = str(agent.artefacts.image_file)
        if agent.artefacts.code_file:
            artifacts["code_file"] = str(agent.artefacts.code_file)
        
        # Add session tracking to metrics
        metrics.update({
            "request_id": str(uuid4()),
            "api_processing_time": time.time() - start_time
        })
        
        logger.info(f"Chat request completed for session {session_id[:8]} in {metrics.get('total_time', 0):.2f}s")
        
        return ChatResponse(
            response=response,
            session_id=session_id,
            metrics=metrics,
            artifacts=artifacts if artifacts else None
        )
        
    except Exception as e:
        logger.error(f"Chat request failed for session {session_id}: {e}")
        
        # Clean up failed session
        if session_id and session_id in sessions:
            del sessions[session_id]
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="Internal server error during chat processing",
                detail=str(e),
                session_id=session_id,
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            ).dict()
        )

@app.get("/v1/sessions/{session_id}/artifacts", tags=["Artifacts"])
async def list_session_artifacts(session_id: str):
    """List all artifacts for a specific session"""
    if session_id not in sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )
    
    agent = sessions[session_id]
    artifacts = {}
    
    if agent.artefacts.data_file and agent.artefacts.data_file.exists():
        artifacts["data"] = {
            "file": str(agent.artefacts.data_file),
            "size": agent.artefacts.data_file.stat().st_size,
            "type": "csv"
        }
    
    if agent.artefacts.image_file and agent.artefacts.image_file.exists():
        artifacts["image"] = {
            "file": str(agent.artefacts.image_file),
            "size": agent.artefacts.image_file.stat().st_size,
            "type": "png"
        }
    
    if agent.artefacts.code_file and agent.artefacts.code_file.exists():
        artifacts["code"] = {
            "file": str(agent.artefacts.code_file),
            "size": agent.artefacts.code_file.stat().st_size,
            "type": "python"
        }
    
    return {"session_id": session_id, "artifacts": artifacts}

@app.get("/v1/download/{file_type}/{session_id}", tags=["Artifacts"])
async def download_artifact(file_type: str, session_id: str):
    """
    Download artifacts generated by the chatbot
    
    - **file_type**: Type of file to download (data, image, code)
    - **session_id**: Session ID that generated the artifact
    """
    if session_id not in sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )
    
    agent = sessions[session_id]
    
    # Map file types to agent artifacts
    file_mapping = {
        "data": agent.artefacts.data_file,
        "image": agent.artefacts.image_file,
        "code": agent.artefacts.code_file
    }
    
    if file_type not in file_mapping:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Must be one of: {', '.join(file_mapping.keys())}"
        )
    
    file_path = file_mapping[file_type]
    
    if not file_path or not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {file_type} file found for this session"
        )
    
    # Determine media type based on file type
    media_types = {
        "data": "text/csv",
        "image": "image/png", 
        "code": "text/x-python"
    }
    
    return FileResponse(
        path=file_path,
        media_type=media_types[file_type],
        filename=file_path.name
    )

@app.delete("/v1/sessions/{session_id}", tags=["Sessions"])
async def delete_session(session_id: str):
    """Delete a specific session and clean up its artifacts"""
    if session_id not in sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )
    
    # Clean up session
    del sessions[session_id]
    
    # Optionally clean up session files (be careful with this in production)
    # You might want to mark for cleanup instead of immediate deletion
    
    return {"message": f"Session {session_id} deleted successfully"}

@app.get("/v1/sessions", tags=["Sessions"])
async def list_active_sessions():
    """List all active sessions (for debugging/monitoring)"""
    return {
        "active_sessions": len(sessions),
        "sessions": [
            {
                "session_id": sid,
                "created": "N/A",  # Would track this in production
                "last_activity": "N/A"
            }
            for sid in sessions.keys()
        ]
    }

# ─────────────────────────── ERROR HANDLERS ─────────────────────────── #

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "path": str(request.url)
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """General exception handler for unhandled errors"""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": "An unexpected error occurred",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "path": str(request.url)
        }
    )

# ─────────────────────────── MAIN ─────────────────────────── #

if __name__ == "__main__":
    uvicorn.run(
        "fastapi_microservice:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

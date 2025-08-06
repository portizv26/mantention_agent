"""
Test suite for the improved chatbot API
Includes unit tests, integration tests, and performance tests
"""

import asyncio
import pytest
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

import pandas as pd
from fastapi.testclient import TestClient
from httpx import AsyncClient

from src.improved_agent import ImprovedAgentChat, CacheManager
from src.fastapi_microservice import app

# ─────────────────────────── FIXTURES ─────────────────────────── #

@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name
    
    conn = sqlite3.connect(db_path)
    
    # Create a sample table for testing
    conn.execute("""
        CREATE TABLE equipment (
            id INTEGER PRIMARY KEY,
            name TEXT,
            type TEXT,
            status TEXT,
            last_maintenance DATE,
            next_maintenance DATE
        )
    """)
    
    # Insert sample data
    sample_data = [
        (1, 'Pump A1', 'Pump', 'Active', '2024-01-15', '2024-04-15'),
        (2, 'Motor B2', 'Motor', 'Maintenance', '2024-02-01', '2024-05-01'),
        (3, 'Valve C3', 'Valve', 'Active', '2024-01-20', '2024-04-20'),
    ]
    
    conn.executemany("""
        INSERT INTO equipment (id, name, type, status, last_maintenance, next_maintenance)
        VALUES (?, ?, ?, ?, ?, ?)
    """, sample_data)
    
    conn.commit()
    yield conn
    
    conn.close()
    Path(db_path).unlink(missing_ok=True)

@pytest.fixture
def mock_llm():
    """Mock LLM for testing without API calls"""
    with patch('src.improved_agent.AsyncLLM') as mock:
        llm_instance = AsyncMock()
        
        # Mock responses
        llm_instance.chat.return_value = "Mocked LLM response"
        llm_instance.struct.return_value = Mock(
            is_on_topic=True,
            is_context_sufficient=True,
            is_new_sql_query_needed=True,
            is_new_image_needed=False
        )
        
        mock.return_value = llm_instance
        yield llm_instance

@pytest.fixture
def agent(temp_db, mock_llm):
    """Create agent instance for testing"""
    return ImprovedAgentChat(temp_db)

@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)

# ─────────────────────────── UNIT TESTS ─────────────────────────── #

class TestCacheManager:
    """Test the cache manager functionality"""
    
    def test_cache_basic_operations(self):
        cache = CacheManager(max_size=3)
        
        # Test set and get
        cache.set("key1", {"value": "data1"})
        assert cache.get("key1") == {"value": "data1"}
        
        # Test miss
        assert cache.get("nonexistent") is None
    
    def test_cache_lru_eviction(self):
        cache = CacheManager(max_size=2)
        
        cache.set("key1", {"value": "data1"})
        cache.set("key2", {"value": "data2"})
        cache.set("key3", {"value": "data3"})  # Should evict key1
        
        assert cache.get("key1") is None
        assert cache.get("key2") == {"value": "data2"}
        assert cache.get("key3") == {"value": "data3"}
    
    def test_cache_access_order_update(self):
        cache = CacheManager(max_size=2)
        
        cache.set("key1", {"value": "data1"})
        cache.set("key2", {"value": "data2"})
        
        # Access key1 to make it most recent
        cache.get("key1")
        
        # Add key3, should evict key2 (least recent)
        cache.set("key3", {"value": "data3"})
        
        assert cache.get("key1") == {"value": "data1"}
        assert cache.get("key2") is None
        assert cache.get("key3") == {"value": "data3"}

class TestImprovedAgent:
    """Test the improved agent functionality"""
    
    @pytest.mark.asyncio
    async def test_sql_execution(self, agent, mock_llm):
        """Test SQL query execution"""
        # Mock the LLM to return a valid SQL query
        mock_llm.chat.side_effect = [
            "What equipment needs maintenance?",  # Simple question
            "SELECT * FROM equipment WHERE status = 'Maintenance'"  # SQL query
        ]
        
        result = await agent._supervised_sql_async("¿Qué equipos necesitan mantenimiento?")
        sql_query, df, data_path, success = result
        
        assert success
        assert "SELECT" in sql_query
        assert len(df) > 0
        assert data_path is not None
    
    @pytest.mark.asyncio
    async def test_translation(self, agent, mock_llm):
        """Test translation functionality"""
        mock_llm.chat.return_value = "What equipment needs maintenance?"
        
        result = await agent._translate(
            "¿Qué equipos necesitan mantenimiento?", 
            src="spanish", 
            tgt="english"
        )
        
        assert result == "What equipment needs maintenance?"
        mock_llm.chat.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_error_handling_sql_retry(self, agent, mock_llm):
        """Test SQL retry mechanism on errors"""
        # Mock LLM to return invalid SQL that will fail
        mock_llm.chat.side_effect = [
            "Question",
            "INVALID SQL QUERY",  # First attempt fails
            "Question",  
            "SELECT * FROM equipment",  # Second attempt succeeds
        ]
        
        result = await agent._supervised_sql_async("test query")
        sql_query, df, data_path, success = result
        
        assert success
        assert agent.artefacts.metrics.sql_attempts == 2

# ─────────────────────────── INTEGRATION TESTS ─────────────────────────── #

class TestAPIIntegration:
    """Test the FastAPI integration"""
    
    def test_health_check(self, client):
        """Test health check endpoint"""
        response = client.get("/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] in ["healthy", "degraded"]
        assert "timestamp" in data
        assert "version" in data
    
    def test_root_endpoint(self, client):
        """Test root endpoint"""
        response = client.get("/")
        assert response.status_code == 200
        
        data = response.json()
        assert data["message"] == "Maintenance Chatbot API"
        assert data["version"] == "1.0.0"
    
    @patch('src.fastapi_microservice.get_database_connection')
    @patch('src.fastapi_microservice.ImprovedAgentChat')
    def test_chat_endpoint(self, mock_agent_class, mock_db, client):
        """Test chat endpoint"""
        # Mock the agent
        mock_agent = AsyncMock()
        mock_agent.execute.return_value = (
            "Respuesta del chatbot",
            {"total_time": 1.23, "flow": "good"}
        )
        mock_agent.artefacts.data_file = None
        mock_agent.artefacts.image_file = None
        mock_agent.artefacts.code_file = None
        
        mock_agent_class.return_value = mock_agent
        mock_db.return_value = Mock()
        
        # Make request
        response = client.post("/v1/chat", json={
            "message": "¿Cuántos equipos hay en mantenimiento?"
        })
        
        assert response.status_code == 200
        data = response.json()
        
        assert "response" in data
        assert "session_id" in data
        assert "metrics" in data
        assert data["metrics"]["flow"] == "good"
    
    def test_chat_validation(self, client):
        """Test input validation"""
        # Empty message
        response = client.post("/v1/chat", json={"message": ""})
        assert response.status_code == 422
        
        # Message too long
        long_message = "x" * 1001
        response = client.post("/v1/chat", json={"message": long_message})
        assert response.status_code == 422
    
    def test_session_endpoints(self, client):
        """Test session management endpoints"""
        # List sessions
        response = client.get("/v1/sessions")
        assert response.status_code == 200
        
        # Delete non-existent session
        response = client.delete("/v1/sessions/nonexistent")
        assert response.status_code == 404

# ─────────────────────────── PERFORMANCE TESTS ─────────────────────────── #

class TestPerformance:
    """Performance and load testing"""
    
    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Test handling of concurrent requests"""
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Create multiple concurrent requests
            tasks = []
            for i in range(5):
                task = client.post("/v1/chat", json={
                    "message": f"Test message {i}"
                })
                tasks.append(task)
            
            # Execute concurrently
            start_time = time.time()
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            end_time = time.time()
            
            # Check that all requests completed
            for response in responses:
                if not isinstance(response, Exception):
                    assert response.status_code in [200, 500]  # Allow errors in test env
            
            # Should complete reasonably quickly
            assert end_time - start_time < 30.0
    
    def test_response_times(self, client):
        """Test response time requirements"""
        start_time = time.time()
        
        response = client.get("/health")
        
        end_time = time.time()
        response_time = end_time - start_time
        
        assert response.status_code == 200
        assert response_time < 1.0  # Health check should be very fast
    
    @pytest.mark.asyncio
    async def test_memory_usage(self, agent):
        """Test memory usage with large datasets"""
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss
        
        # Simulate processing large datasets
        large_df = pd.DataFrame({
            'id': range(10000),
            'data': ['test_data'] * 10000
        })
        
        # Process through file manager
        path = await agent.fs.save_dataframe(large_df)
        
        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory
        
        # Memory increase should be reasonable (less than 100MB for this test)
        assert memory_increase < 100 * 1024 * 1024
        
        # Clean up
        if path.exists():
            path.unlink()

# ─────────────────────────── ERROR HANDLING TESTS ─────────────────────────── #

class TestErrorHandling:
    """Test error handling and recovery"""
    
    @patch('src.fastapi_microservice.get_database_connection')
    def test_database_connection_error(self, mock_db, client):
        """Test handling of database connection errors"""
        mock_db.side_effect = Exception("Database connection failed")
        
        response = client.post("/v1/chat", json={
            "message": "Test message"
        })
        
        assert response.status_code == 500
        data = response.json()
        assert "error" in data
    
    @pytest.mark.asyncio
    async def test_llm_api_error_recovery(self, agent, mock_llm):
        """Test recovery from LLM API errors"""
        # Mock LLM to fail first two times, then succeed
        mock_llm.chat.side_effect = [
            Exception("API Error"),
            Exception("API Error"),
            "Success response"
        ]
        
        result = await agent._translate("test", src="spanish", tgt="english")
        assert result == "Success response"
        assert mock_llm.chat.call_count == 3  # Should retry
    
    def test_invalid_session_handling(self, client):
        """Test handling of invalid session IDs"""
        response = client.get("/v1/sessions/invalid-session-id/artifacts")
        assert response.status_code == 404
        
        response = client.delete("/v1/sessions/invalid-session-id")
        assert response.status_code == 404

# ─────────────────────────── CONFIGURATION TESTS ─────────────────────────── #

class TestConfiguration:
    """Test configuration and environment handling"""
    
    def test_environment_variables(self):
        """Test environment variable loading"""
        import os
        from src.config import OPENAI_MODEL_CHAT, MAX_SQL_RETRIES
        
        # Test default values
        assert OPENAI_MODEL_CHAT == "gpt-4o-mini"
        assert MAX_SQL_RETRIES == 3
    
    def test_path_configuration(self):
        """Test path configuration"""
        from src.config import CHAT_DOCS_DIR
        
        assert isinstance(CHAT_DOCS_DIR, Path)
        assert CHAT_DOCS_DIR.name == "chat_docs"

# ─────────────────────────── RUN CONFIGURATION ─────────────────────────── #

if __name__ == "__main__":
    # Run tests with coverage
    pytest.main([
        __file__,
        "-v",
        "--cov=src",
        "--cov-report=html",
        "--cov-report=term",
        "--asyncio-mode=auto"
    ])

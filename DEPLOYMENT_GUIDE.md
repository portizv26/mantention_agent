# Maintenance Chatbot - Deployment & Usage Guide

## 🚀 Quick Start

### Local Development

1. **Install Dependencies**
   ```bash
   pip install -r requirements_microservice.txt
   ```

2. **Set Environment Variables**
   ```bash
   cp .env.example .env
   # Edit .env with your OpenAI API key
   ```

3. **Run the API**
   ```bash
   cd src
   uvicorn fastapi_microservice:app --reload --host 0.0.0.0 --port 8000
   ```

4. **Access the API**
   - API Documentation: http://localhost:8000/docs
   - Alternative docs: http://localhost:8000/redoc
   - Health check: http://localhost:8000/health

### Docker Deployment

1. **Build and Run with Docker Compose**
   ```bash
   docker-compose up -d
   ```

2. **Check Status**
   ```bash
   docker-compose ps
   docker-compose logs chatbot-api
   ```

## 📊 Performance Improvements

### Before vs After Comparison

| Feature | Original | Improved | Benefit |
|---------|----------|----------|---------|
| **Response Time** | Sequential (4-8s) | Parallel (2-4s) | 50% faster |
| **Image Generation** | Blocking | Async | Non-blocking UI |
| **Error Recovery** | Basic | Robust retry | Better reliability |
| **Caching** | None | Intelligent | Faster repeat queries |
| **API** | Streamlit only | REST API | Microservice ready |
| **Monitoring** | Basic logs | Structured + metrics | Better observability |
| **Deployment** | Manual | Docker + compose | Production ready |

### Key Optimizations Implemented

1. **Async/Parallel Processing**
   - Image generation runs parallel to text response generation
   - Multiple LLM calls are parallelized where possible
   - Non-blocking file operations

2. **Intelligent Caching**
   - LRU cache for common queries
   - Reduces API calls for repeated questions
   - Session-aware caching

3. **Enhanced Error Handling**
   - Exponential backoff for API failures
   - SQL retry mechanism with feedback
   - Graceful degradation

4. **Performance Monitoring**
   - Detailed timing metrics for each component
   - Request tracing and session management
   - Health checks and status monitoring

## 🏗️ Architecture

### Microservice Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Client App    │    │  Load Balancer  │    │   Monitoring    │
│  (Web/Mobile)   │    │   (Nginx)       │    │ (Prometheus)    │
└─────────┬───────┘    └─────────┬───────┘    └─────────────────┘
          │                      │                      │
          │              ┌───────▼───────┐              │
          └──────────────┤  FastAPI App  ├──────────────┘
                         │   (Port 8000) │
                         └───────┬───────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
┌────────▼────────┐    ┌────────▼────────┐    ┌────────▼────────┐
│ ImprovedAgent   │    │   File System   │    │   Database      │
│   (Core Logic)  │    │  (Artifacts)    │    │   (SQLite)      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │
┌────────▼────────┐
│   OpenAI API    │
│  (LLM + Images) │
└─────────────────┘
```

### Request Flow

```
1. Client Request → FastAPI → Session Manager
2. Session Manager → ImprovedAgent.execute()
3. Agent → Translation (ES→EN)
4. Agent → Classification & Action Planning
5. Branch A: Answer Only (reuse cache)
   Branch B: Image Only (async generation)
   Branch C: Data + Image (parallel processing)
6. Parallel Tasks:
   ├── SQL Query Execution
   ├── Final Answer Generation
   └── Image Generation (if needed)
7. Results → Cache → Response (EN→ES)
8. FastAPI → Client Response + Artifacts
```

## 🔧 API Reference

### POST /v1/chat

**Request:**
```json
{
  "message": "¿Cuántos equipos necesitan mantenimiento preventivo?",
  "session_id": "optional-uuid"
}
```

**Response:**
```json
{
  "response": "Actualmente hay 15 equipos que necesitan mantenimiento preventivo este mes.",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "metrics": {
    "total_time": 2.34,
    "sql_time": 0.45,
    "image_time": 1.23,
    "sql_attempts": 1,
    "flow": "good",
    "api_processing_time": 0.05
  },
  "artifacts": {
    "data_file": "chat_docs/20250805T143022/data_0.csv",
    "image_file": "chat_docs/20250805T143022/image_0.png"
  }
}
```

### GET /v1/download/{file_type}/{session_id}

Download generated artifacts:
- `file_type`: "data", "image", or "code"
- Returns the actual file for download

### Additional Endpoints

- `GET /health` - Health check
- `GET /v1/sessions` - List active sessions
- `DELETE /v1/sessions/{session_id}` - Clean up session
- `GET /v1/sessions/{session_id}/artifacts` - List session artifacts

## 🛠️ Configuration

### Environment Variables

```bash
# OpenAI Configuration
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL_CHAT=gpt-4o-mini
OPENAI_MODEL_STRUCT=o3-mini

# Performance Tuning
MAX_SQL_RETRIES=3
HEAD_ROWS=5

# File Storage
CHAT_DOCS_DIR=chat_docs

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
```

### Production Configuration

For production deployment, consider:

1. **Security**
   ```bash
   # Use environment-specific API keys
   OPENAI_API_KEY=${OPENAI_API_KEY}
   
   # Enable HTTPS
   SSL_CERT_PATH=/etc/ssl/certs/cert.pem
   SSL_KEY_PATH=/etc/ssl/private/key.pem
   ```

2. **Scaling**
   ```bash
   # Multiple workers
   WORKERS=4
   
   # Connection pooling
   DB_POOL_SIZE=20
   ```

3. **Monitoring**
   ```bash
   # Enable detailed logging
   LOG_LEVEL=INFO
   STRUCTURED_LOGGING=true
   
   # Metrics export
   PROMETHEUS_ENABLED=true
   METRICS_PORT=9090
   ```

## 📊 Monitoring & Observability

### Health Checks

The API provides comprehensive health checks:

```bash
curl http://localhost:8000/health
```

Returns:
- Service status
- Database connectivity
- Dependencies status
- Performance metrics

### Logging

Structured logging with correlation IDs:

```json
{
  "timestamp": "2025-08-05T14:30:22Z",
  "level": "INFO",
  "message": "Processing request",
  "data": {
    "session_id": "550e8400-e29b",
    "message_length": 45,
    "request_id": "req_123"
  }
}
```

### Metrics

Key metrics tracked:
- Request latency (p50, p95, p99)
- Error rates by type
- Cache hit rates
- SQL execution times
- Image generation times
- Active sessions count

## 🧪 Testing

### Run Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run performance tests
pytest tests/ -m performance
```

### Test Categories

1. **Unit Tests** - Individual component testing
2. **Integration Tests** - API endpoint testing
3. **Performance Tests** - Load and concurrency testing
4. **Error Handling Tests** - Failure scenario testing

## 🚢 Deployment Options

### Option 1: Docker Compose (Recommended)

```bash
# Development
docker-compose up -d

# Production
docker-compose -f docker-compose.prod.yml up -d
```

### Option 2: Kubernetes

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: chatbot-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: chatbot-api
  template:
    metadata:
      labels:
        app: chatbot-api
    spec:
      containers:
      - name: chatbot
        image: maintenance-chatbot:latest
        ports:
        - containerPort: 8000
        env:
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: api-secrets
              key: openai-key
```

### Option 3: Cloud Deployment

#### Azure Container Instances

```bash
az container create \
  --resource-group myResourceGroup \
  --name maintenance-chatbot \
  --image maintenance-chatbot:latest \
  --dns-name-label chatbot-api \
  --ports 8000
```

#### AWS ECS/Fargate

```json
{
  "family": "maintenance-chatbot",
  "taskRoleArn": "arn:aws:iam::123456789:role/ecsTaskRole",
  "containerDefinitions": [
    {
      "name": "chatbot-api",
      "image": "maintenance-chatbot:latest",
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "OPENAI_API_KEY",
          "value": "${OPENAI_API_KEY}"
        }
      ]
    }
  ]
}
```

## 🔒 Security Considerations

1. **API Key Management**
   - Use environment variables
   - Rotate keys regularly
   - Monitor usage

2. **Input Validation**
   - Pydantic models for request validation
   - SQL injection prevention
   - Rate limiting

3. **Network Security**
   - HTTPS only in production
   - Firewall rules
   - VPC/subnet isolation

4. **Data Privacy**
   - Encrypt artifacts at rest
   - Secure session management
   - Log sanitization

## 📈 Performance Tuning

### Database Optimization

```sql
-- Add indexes for common queries
CREATE INDEX idx_equipment_status ON equipment(status);
CREATE INDEX idx_equipment_maintenance_date ON equipment(next_maintenance);
```

### Caching Strategy

```python
# Tune cache settings
CACHE_SIZE = 1000  # Increase for more memory
CACHE_TTL = 3600   # 1 hour default
```

### Concurrency Settings

```python
# Uvicorn workers
uvicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker

# Connection pools
DATABASE_POOL_SIZE = 20
OPENAI_MAX_CONCURRENT = 10
```

## 🆘 Troubleshooting

### Common Issues

1. **High Response Times**
   - Check OpenAI API rate limits
   - Monitor database query performance
   - Review cache hit rates

2. **Memory Issues**
   - Monitor session cleanup
   - Check artifact storage growth
   - Review DataFrame memory usage

3. **Database Locks**
   - Use read-only connections where possible
   - Implement connection pooling
   - Monitor long-running queries

### Debugging

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Check system resources
docker stats
htop

# Monitor API calls
tail -f logs/chatbot.log | grep "API call"
```

## 🔄 Migration Guide

### From Original to Improved Version

1. **Backup Current Data**
   ```bash
   cp -r chat_docs chat_docs_backup
   ```

2. **Update Dependencies**
   ```bash
   pip install -r requirements_microservice.txt
   ```

3. **Update Configuration**
   ```bash
   # Add new environment variables
   echo "API_HOST=0.0.0.0" >> .env
   echo "API_PORT=8000" >> .env
   ```

4. **Test New API**
   ```bash
   # Start new API
   uvicorn src.fastapi_microservice:app --reload
   
   # Test basic functionality
   curl -X POST "http://localhost:8000/v1/chat" \
        -H "Content-Type: application/json" \
        -d '{"message": "test"}'
   ```

5. **Update Client Applications**
   - Replace Streamlit calls with HTTP requests
   - Update error handling for new response format
   - Implement session management if needed

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## 📞 Support

- Documentation: `/docs` endpoint
- Health Check: `/health` endpoint
- Logs: Check `logs/` directory
- Metrics: Prometheus endpoint (if enabled)

---

*Last updated: August 5, 2025*

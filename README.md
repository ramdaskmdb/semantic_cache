# Semantic Cache with Databricks Lakebase

A production-ready semantic cache implementation using:
- **Databricks Lakebase** (managed PostgreSQL) for storage
- **pgvector** extension for vector similarity search
- **Databricks Foundation Model API** for generating embeddings

## Features

- ✅ Semantic matching based on query meaning, not exact text
- ✅ Configurable similarity threshold
- ✅ Automatic token refresh for Lakebase OAuth
- ✅ Hit count tracking and statistics
- ✅ Metadata support for cache entries
- ✅ Fast vector similarity search with IVFFlat indexing

## Architecture

```
User Query
    ↓
Generate Embedding (Databricks Foundation Model API)
    ↓
Vector Similarity Search (pgvector)
    ↓
Cosine Similarity > Threshold?
    ├─ Yes → Return cached response (update hit count)
    └─ No  → Cache miss

Authentication Flow:
1. Initialize SemanticCache with Databricks profile
2. Use Databricks SDK to generate OAuth token
3. Retrieve Lakebase host and user email
4. Connect to PostgreSQL with token (expires after 1 hour)
5. Auto-refresh token on connection failure
```

## Prerequisites

1. **Databricks Workspace** with FE-VM support
   - Get one using: `/databricks-fe-vm-workspace-deployment`

2. **Databricks CLI** (v0.285.0+)
   ```bash
   databricks --version
   databricks auth login --host <workspace-url> --profile <profile-name>
   ```

3. **PostgreSQL client** (for setup script)
   ```bash
   brew install postgresql@16  # macOS
   ```

4. **Python dependencies**
   ```bash
   pip install -r requirements.txt
   # or with uv:
   uv pip install -r requirements.txt
   ```
   
   Required packages:
   - `psycopg2-binary>=2.9.9` - PostgreSQL adapter
   - `requests>=2.31.0` - HTTP library
   - `databricks-sdk>=0.20.0` - Databricks SDK for authentication and API access

## Quick Start

### Step 1: Set up Lakebase

Run the setup script to create the Lakebase project and database:

```bash
chmod +x setup_lakebase.sh
./setup_lakebase.sh <your-databricks-profile>
```

This creates:
- A Lakebase project with the specified PROJECT_ID (e.g., `rkm-semantic-cache`)
- A database with the specified DATABASE_NAME (e.g., `rkm_cache_db`)
- A table `semantic_cache` with pgvector extension enabled
- A connection config file `lakebase_connection.json`

**Note**: The script is idempotent - if resources already exist, it will skip creation and just update the config file.
The project takes ~2 minutes to provision on first creation.

### Step 2: Run the example

```bash
# Using standard Python
python example_usage.py

# Or with uv (automatically installs dependencies from pyproject.toml)
uv run example_usage.py
```

## Usage

### Initialize the cache

```python
from semantic_cache import create_cache_from_config

# Create cache from config file (recommended)
cache = create_cache_from_config("lakebase_connection.json")

# Or initialize manually
from semantic_cache import SemanticCache

cache = SemanticCache(
    lakebase_profile="your-databricks-profile",
    lakebase_endpoint="projects/PROJECT_ID/branches/production/endpoints/primary",
    lakebase_database="cache_db",
    similarity_threshold=0.85,  # Adjust as needed
    embedding_model="databricks-bge-large-en"
)
```

### Add a cache entry

```python
cache.set(
    query="What is the capital of France?",
    response="The capital of France is Paris.",
    metadata={"source": "geography", "confidence": 1.0}
)
```

### Retrieve from cache

```python
# Simple retrieval (returns string or None)
response = cache.get("What's the capital city of France?")
if response:
    print(f"Cache hit: {response}")
else:
    print("Cache miss")

# With metadata (returns tuple or None)
result = cache.get("What's the capital city of France?", return_metadata=True)
if result:
    response, similarity, metadata = result
    print(f"Response: {response}")
    print(f"Similarity: {similarity:.3f}")  # Float between 0.0 and 1.0
    print(f"Metadata: {metadata}")          # Dict (automatically deserialized from JSONB)
```

**Return values**:
- `return_metadata=False`: Returns `str` (response) or `None` if no match
- `return_metadata=True`: Returns `tuple[str, float, dict]` (response, similarity, metadata) or `None`

### Search for similar queries

```python
# Search returns a list of dictionaries with cache entries and similarity scores
results = cache.search("French capital", top_k=5, min_similarity=0.7)
for result in results:
    print(f"Query: {result['query_text']}")
    print(f"Similarity: {result['similarity']:.3f}")
    print(f"Response: {result['response']}")
    print(f"Hit count: {result['hit_count']}")
    print(f"Created: {result['created_at']}")
```

**Result fields**:
- `id`: Cache entry ID (int)
- `query_text`: Original cached query (str)
- `response`: Cached response (str)
- `metadata`: Metadata dictionary (dict)
- `similarity`: Cosine similarity score (float, 0.0-1.0)
- `hit_count`: Number of times this entry has been retrieved (int)
- `created_at`: ISO timestamp of creation (str)
- `last_accessed_at`: ISO timestamp of last access (str)

### Get statistics

```python
stats = cache.stats()
print(f"Total entries: {stats['total_entries']}")
print(f"Total hits: {stats['total_hits']}")
print(f"Average hits per entry: {stats['avg_hits_per_entry']:.2f}")
```

### Clear cache

```python
# Delete specific entry
cache.delete(cache_id=123)

# Clear all entries
deleted_count = cache.clear()
```

## Configuration

### Configuration File Format

The `lakebase_connection.json` file should contain:

```json
{
  "profile": "your-databricks-profile",
  "endpoint": "projects/PROJECT_ID/branches/production/endpoints/primary",
  "database_name": "your_database_name",
  "similarity_threshold": 0.85,
  "embedding_model": "databricks-bge-large-en"
}
```

**Note**: The `endpoint` field must be the full path including the project, branch, and endpoint name. 
The setup script generates this automatically.

### Similarity Threshold

The `similarity_threshold` parameter controls how similar a query must be to return a cached result.
It uses cosine similarity (1 - cosine distance), where 1.0 = identical and 0.0 = completely different:

- **0.95+**: Very strict, only near-identical queries
- **0.85-0.95**: Recommended range for most use cases (default: 0.85)
- **0.70-0.85**: More lenient, catches paraphrases
- **<0.70**: May return false positives

The implementation calculates: `1 - (query_embedding <=> cached_embedding)` where `<=>` is pgvector's
cosine distance operator. Results are only returned if similarity >= threshold.

### Embedding Model

The default model is `databricks-bge-large-en` (1024 dimensions). Other options:

- `databricks-gte-large-en` (1024 dimensions)
- Custom models deployed to Databricks Model Serving

To use a different model, update the `embedding_model` parameter when initializing the cache.

## Database Schema

```sql
CREATE TABLE semantic_cache (
    id SERIAL PRIMARY KEY,
    query_text TEXT NOT NULL,
    query_embedding vector(1024),  -- BGE embeddings are 1024-dimensional
    response TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',   -- Automatically deserialized by psycopg2
    hit_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    last_accessed_at TIMESTAMP DEFAULT NOW()
);

-- IVFFlat index for fast vector similarity search
CREATE INDEX semantic_cache_embedding_idx ON semantic_cache
USING ivfflat (query_embedding vector_cosine_ops) WITH (lists = 100);
```

**Notes**:
- The `metadata` field uses PostgreSQL JSONB type for efficient storage and querying
- `psycopg2` with `RealDictCursor` automatically deserializes JSONB to Python dicts
- The `query_embedding` uses pgvector's `vector(1024)` type for efficient similarity search
- `hit_count` tracks how many times a cached entry has been retrieved

## Token Expiry

Lakebase OAuth tokens expire after 1 hour. The `SemanticCache` class automatically refreshes tokens when they expire by:
1. Detecting `psycopg2.OperationalError` on connection failure
2. Calling `_refresh_lakebase_connection()` to generate a new token
3. Retrying the connection with the new credentials

This is handled transparently - you don't need to manage token refresh manually.

To manually regenerate a token for debugging:

```bash
databricks postgres generate-database-credential \
  projects/PROJECT_ID/branches/production/endpoints/primary \
  -p <profile> -o json | jq -r '.token'
```

## Performance Tips

1. **Adjust IVFFlat index lists**: For >100k entries, increase the `lists` parameter:
   ```sql
   CREATE INDEX semantic_cache_embedding_idx ON semantic_cache
   USING ivfflat (query_embedding vector_cosine_ops) WITH (lists = 1000);
   ```

2. **Scale Lakebase endpoint**: Increase compute for better performance:
   ```bash
   databricks postgres update-endpoint \
     projects/PROJECT_ID/branches/production/endpoints/primary \
     "spec.autoscaling_limit_min_cu,spec.autoscaling_limit_max_cu" \
     --json '{"spec": {"autoscaling_limit_min_cu": 1.0, "autoscaling_limit_max_cu": 4.0}}' \
     -p <profile>
   ```

3. **Use connection pooling**: For high-throughput applications, implement connection pooling with `psycopg2.pool`.

## Security

The implementation follows security best practices:

- **Parameterized queries**: All SQL queries use parameterized placeholders (`%s`) to prevent SQL injection
- **SSL/TLS connections**: All connections to Lakebase use `sslmode='require'`
- **OAuth authentication**: Uses Databricks OAuth tokens that auto-expire after 1 hour
- **Automatic token refresh**: Tokens are regenerated on-demand, never stored persistently

## Use Cases

- **LLM Response Caching**: Reduce API costs by caching similar prompts
- **RAG Systems**: Cache retrieval results for common queries
- **Customer Support**: Return answers to semantically similar questions
- **API Gateway**: Cache responses for similar API requests
- **Data Processing**: Cache expensive computation results

## Cleanup

To delete the Lakebase project and all data:

```bash
databricks postgres delete-project projects/PROJECT_ID -p <profile>

# Example:
databricks postgres delete-project projects/rkm-semantic-cache -p e2demofieldeng
```

## Troubleshooting

### "unknown command postgres"
Your Databricks CLI is below v0.285.0. Upgrade to the latest version.

### Connection refused
Ensure the endpoint is in ACTIVE state:
```bash
databricks postgres list-endpoints projects/PROJECT_ID/branches/production \
  -p <profile> -o json | jq '.[].status.current_state'
```

### "permission denied for schema public"
Make sure you created the database first with `CREATE DATABASE your_database_name`.

### Authentication failed
Token expired. The cache will auto-refresh automatically on the next connection attempt. 
For manual refresh:
```bash
databricks postgres generate-database-credential \
  projects/PROJECT_ID/branches/production/endpoints/primary \
  -p <profile>
```

### "database does not exist"
Run the setup script to create the database:
```bash
./setup_lakebase.sh <profile>
```

## License

This implementation is provided as-is for use with Databricks services.

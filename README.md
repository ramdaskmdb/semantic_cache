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
Generate Embedding (Databricks BGE Model)
    ↓
Vector Similarity Search (pgvector)
    ↓
Cosine Similarity > Threshold?
    ├─ Yes → Return cached response
    └─ No  → Cache miss
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
   ```

## Quick Start

### Step 1: Set up Lakebase

Run the setup script to create the Lakebase project and database:

```bash
chmod +x setup_lakebase.sh
./setup_lakebase.sh <your-databricks-profile>
```

This creates:
- A Lakebase Autoscaling project named `semantic-cache`
- A database named `cache_db`
- A table `semantic_cache` with pgvector extension enabled
- A connection config file `lakebase_connection.json`

### Step 2: Run the example

```bash
python example_usage.py
```

## Usage

### Initialize the cache

```python
from semantic_cache import create_cache_from_config

# Create cache from config file
cache = create_cache_from_config("lakebase_connection.json")

# Or initialize manually
from semantic_cache import SemanticCache

cache = SemanticCache(
    databricks_host="https://your-workspace.cloud.databricks.com",
    databricks_token="your-token",
    lakebase_host="your-lakebase-host",
    lakebase_profile="your-profile",
    lakebase_project_id="semantic-cache",
    lakebase_database="cache_db",
    similarity_threshold=0.85  # Adjust as needed
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
# Simple retrieval
response = cache.get("What's the capital city of France?")
if response:
    print(f"Cache hit: {response}")
else:
    print("Cache miss")

# With metadata
result = cache.get("What's the capital city of France?", return_metadata=True)
if result:
    response, similarity, metadata = result
    print(f"Response: {response}")
    print(f"Similarity: {similarity:.3f}")
    print(f"Metadata: {metadata}")
```

### Search for similar queries

```python
results = cache.search("French capital", top_k=5, min_similarity=0.7)
for result in results:
    print(f"Query: {result['query_text']}")
    print(f"Similarity: {result['similarity']:.3f}")
    print(f"Response: {result['response']}")
```

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

### Similarity Threshold

The `similarity_threshold` parameter controls how similar a query must be to return a cached result:

- **0.95+**: Very strict, only near-identical queries
- **0.85-0.95**: Recommended range for most use cases
- **0.70-0.85**: More lenient, catches paraphrases
- **<0.70**: May return false positives

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
    metadata JSONB DEFAULT '{}',
    hit_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    last_accessed_at TIMESTAMP DEFAULT NOW()
);

-- IVFFlat index for fast vector similarity search
CREATE INDEX semantic_cache_embedding_idx ON semantic_cache
USING ivfflat (query_embedding vector_cosine_ops) WITH (lists = 100);
```

## Token Expiry

Lakebase OAuth tokens expire after 1 hour. The cache automatically refreshes tokens when they expire.

To manually regenerate a token:

```bash
databricks postgres generate-database-credential \
  projects/semantic-cache/branches/production/endpoints/primary \
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
     projects/semantic-cache/branches/production/endpoints/primary \
     "spec.autoscaling_limit_min_cu,spec.autoscaling_limit_max_cu" \
     --json '{"spec": {"autoscaling_limit_min_cu": 1.0, "autoscaling_limit_max_cu": 4.0}}' \
     -p <profile>
   ```

3. **Use connection pooling**: For high-throughput applications, implement connection pooling with `psycopg2.pool`.

## Use Cases

- **LLM Response Caching**: Reduce API costs by caching similar prompts
- **RAG Systems**: Cache retrieval results for common queries
- **Customer Support**: Return answers to semantically similar questions
- **API Gateway**: Cache responses for similar API requests
- **Data Processing**: Cache expensive computation results

## Cleanup

To delete the Lakebase project and all data:

```bash
databricks postgres delete-project projects/semantic-cache -p <profile>
```

## Troubleshooting

### "unknown command postgres"
Your Databricks CLI is below v0.285.0. Upgrade to the latest version.

### Connection refused
Ensure the endpoint is in ACTIVE state:
```bash
databricks postgres list-endpoints projects/semantic-cache/branches/production \
  -p <profile> -o json | jq '.[].status.current_state'
```

### "permission denied for schema public"
Make sure you created the database first with `CREATE DATABASE cache_db`.

### Authentication failed
Token expired. The cache will auto-refresh, but you can manually refresh:
```bash
databricks postgres generate-database-credential \
  projects/semantic-cache/branches/production/endpoints/primary \
  -p <profile>
```

## License

This implementation is provided as-is for use with Databricks services.

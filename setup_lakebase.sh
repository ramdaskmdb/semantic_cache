#!/bin/bash
# Setup script for Lakebase with pgvector for semantic caching
# Usage: ./setup_lakebase.sh <profile-name>

#set -e

PROFILE=${1:-"DEFAULT"}
PROJECT_ID="rkm-semantic-cache"
DATABASE_NAME="rkm_cache_db"

echo "Setting up Lakebase Semantic Cache..."
echo "Profile: $PROFILE"
echo "Project: $PROJECT_ID"

# Step 1: Create Lakebase project
echo "📦 Creating Lakebase project..."
databricks postgres create-project $PROJECT_ID \
  --json '{"spec": {"display_name": "Semantic Cache"}}' \
  --no-wait \
  -p $PROFILE

if [ $? -ne 0 ]; then
  echo "Error: Failed to create Project"
  echo "Lets skip since it may already exist"
else
  echo "Waiting for project to be ready (this takes ~2 minutes)..."
  sleep 120
fi




# Step 2: Get connection details
echo "Getting connection details..."
HOST=$(databricks postgres list-endpoints projects/$PROJECT_ID/branches/production \
  -p $PROFILE -o json | jq -r '.[0].status.hosts.host')
TOKEN=$(databricks postgres generate-database-credential \
  projects/$PROJECT_ID/branches/production/endpoints/primary \
  -p $PROFILE -o json | jq -r '.token')
EMAIL=$(databricks current-user me -p $PROFILE -o json | jq -r '.userName')

echo "Host: $HOST"
echo "User: $EMAIL"

# Step 3: Create database
echo "Creating database..."
PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=postgres user=$EMAIL sslmode=require" \
  -c "CREATE DATABASE $DATABASE_NAME;"


# Step 4: Enable pgvector extension
echo "Enabling pgvector extension..."
PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=$DATABASE_NAME user=$EMAIL sslmode=require" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Step 5: Create semantic cache table
echo "Creating semantic cache table..."
PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=$DATABASE_NAME user=$EMAIL sslmode=require" -c "
CREATE TABLE IF NOT EXISTS semantic_cache (
    id SERIAL PRIMARY KEY,
    query_text TEXT NOT NULL,
    query_embedding vector(1024),  -- Databricks BGE embeddings are 1024-dimensional
    response TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    hit_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    last_accessed_at TIMESTAMP DEFAULT NOW()
);

-- Create index for fast vector similarity search (cosine distance)
CREATE INDEX IF NOT EXISTS semantic_cache_embedding_idx ON semantic_cache
USING ivfflat (query_embedding vector_cosine_ops) WITH (lists = 100);

-- Create index for text search as fallback
CREATE INDEX IF NOT EXISTS semantic_cache_query_text_idx ON semantic_cache USING btree (query_text);
"

# Step 6: Save connection info
echo "Saving connection info..."
cat > lakebase_connection.json <<EOF
{
  "profile": "$PROFILE",
  "endpoint": "projects/$PROJECT_ID/branches/production/endpoints/primary",
  "database_name": "$DATABASE_NAME",
  "similarity_threshold": 0.85,
  "embedding_model": "databricks-bge-large-en"
}
EOF

echo "Setup complete!"
echo ""
echo "Connection info saved to: lakebase_connection.json"
echo ""

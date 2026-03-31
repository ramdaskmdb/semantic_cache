"""
Semantic Cache using Databricks Lakebase with pgvector and Databricks embeddings.

This implementation provides a semantic cache that matches queries based on meaning
rather than exact string matching, using vector embeddings and cosine similarity.

Architecture:
- Uses Databricks Lakebase (managed PostgreSQL) for persistent storage
- Leverages pgvector extension for efficient vector similarity search
- Generates embeddings via Databricks Foundation Model API
- Automatically handles OAuth token refresh (tokens expire after 1 hour)
- Supports metadata, hit tracking, and configurable similarity thresholds

The cache uses cosine distance for similarity matching, where 0.0 = identical
and values closer to 1.0 are less similar. The default threshold of 0.85 works
well for most use cases.
"""

import json
import subprocess
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from urllib import response
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
import requests


class SemanticCache:
    """
    Semantic cache using Databricks Lakebase (Postgres + pgvector) and Databricks embeddings.

    Features:
    - Semantic matching using vector embeddings
    - Configurable similarity threshold
    - Automatic token refresh for Lakebase
    - Hit count tracking
    - Metadata support for cache entries
    """

    def __init__(
        self,
        lakebase_profile: str,
        lakebase_endpoint: str,
        lakebase_database = "semantic_cache_db",
        similarity_threshold: float = 0.85,
        embedding_model: str = "databricks-bge-large-en"
    ):
        """
        Initialize the semantic cache.

        Args:
            lakebase_profile: Databricks CLI profile name
            lakebase_endpoint: Full Lakebase endpoint path (e.g., projects/PROJECT_ID/branches/BRANCH/endpoints/primary)
            lakebase_database: Database name (default: semantic_cache_db)
            similarity_threshold: Minimum cosine similarity for cache hit (default: 0.85)
            embedding_model: Databricks embedding model (default: databricks-bge-large-en)
        """
        self.lakebase_profile = lakebase_profile
        self.lakebase_endpoint = lakebase_endpoint
        self.similarity_threshold = similarity_threshold
        self.embedding_model = embedding_model
        self.lakebase_database = lakebase_database
        self.lakebase_host = None
        # Get user host and initial token
        self._refresh_lakebase_connection()

    def _refresh_lakebase_connection(self):
        """
        Refresh Lakebase connection credentials and host information.

        This method:
        1. Generates a new OAuth token for database authentication
        2. Retrieves the Lakebase host from the endpoint information
        3. Fetches the user's email for database authentication

        Tokens expire after 1 hour and are automatically refreshed on connection failure.
        """
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient(profile=self.lakebase_profile)

        # Generate database credential
        cred = w.postgres.generate_database_credential(endpoint=self.lakebase_endpoint)
        self.lakebase_token = cred.token

        # Extract branch path from endpoint (e.g., "projects/X/branches/Y/endpoints/primary" -> "projects/X/branches/Y")
        branch_path = '/'.join(self.lakebase_endpoint.split('/')[:-2])

        # List endpoints to get host info
        endpoints = list(w.postgres.list_endpoints(branch_path))
        if endpoints:
            self.lakebase_host = endpoints[0].status.hosts.host
        else:
            raise ValueError(f"No endpoints found for branch: {branch_path}")

        # Get user email
        usert = w.current_user.me()
        self.user_email = usert.emails[0].value

    def _get_connection(self) -> psycopg2.extensions.connection:
        """
        Get a connection to Lakebase PostgreSQL.

        Automatically refreshes authentication token if connection fails due to expiration.
        Uses SSL/TLS for secure connections.

        Returns:
            Active psycopg2 connection object
        """
        try:
            return psycopg2.connect(
                host=self.lakebase_host,
                port=5432,
                database=self.lakebase_database,
                user=self.user_email,
                password=self.lakebase_token,
                sslmode='require'
            )
        except psycopg2.OperationalError:
            # Token might have expired, refresh and retry
            self._refresh_lakebase_connection()
            return psycopg2.connect(
                host=self.lakebase_host,
                port=5432,
                database=self.lakebase_database,
                user=self.user_email,
                password=self.lakebase_token,
                sslmode='require'
            )

    def _get_embedding(self, text: str) -> List[float]:
        """
        Generate embeddings for text using Databricks Foundation Model API.

        Args:
            text: Input text to embed

        Returns:
            List of floats representing the embedding vector (1024 dimensions for BGE model)
        """
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient(profile=self.lakebase_profile)
        response = w.serving_endpoints.query(
            name=self.embedding_model,
            input=text)
        return response.data[0].embedding

    def set(
        self,
        query: str,
        response: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Add or update a cache entry.

        Args:
            query: The query text to cache
            response: The response to cache
            metadata: Optional metadata dictionary

        Returns:
            The cache entry ID
        """
        embedding = self._get_embedding(query)
        metadata_json = json.dumps(metadata or {})

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO semantic_cache (query_text, query_embedding, response, metadata)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (query, embedding, response, metadata_json))
                cache_id = cur.fetchone()[0]
                conn.commit()
                return cache_id
        finally:
            conn.close()

    def get(
        self,
        query: str,
        return_metadata: bool = False
    ) -> Optional[str | Tuple[str, float, Dict[str, Any]]]:
        """
        Retrieve a cached response if a semantically similar query exists.

        Args:
            query: The query text to look up
            return_metadata: If True, return (response, similarity_score, metadata) tuple

        Returns:
            - If return_metadata=False: The cached response string or None
            - If return_metadata=True: Tuple of (response, similarity_score, metadata) or None
        """
        embedding = self._get_embedding(query)

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Find most similar cached query using cosine similarity
                # In pgvector, <=> is cosine distance (1 - cosine similarity)
                cur.execute("""
                    SELECT
                        id,
                        query_text,
                        response,
                        metadata,
                        1 - (query_embedding <=> %s::vector) AS similarity
                    FROM semantic_cache
                    WHERE query_embedding IS NOT NULL
                    ORDER BY query_embedding <=> %s::vector
                    LIMIT 1
                """, (embedding, embedding))

                result = cur.fetchone()

                if result and result['similarity'] >= self.similarity_threshold:
                    # Update hit count and last accessed time
                    cur.execute("""
                        UPDATE semantic_cache
                        SET hit_count = hit_count + 1,
                            last_accessed_at = NOW()
                        WHERE id = %s
                    """, (result['id'],))
                    conn.commit()

                    if return_metadata:
                        return (
                            result['response'],
                            float(result['similarity']),
                            result['metadata']
                        )
                    else:
                        return result['response']

                return None
        finally:
            conn.close()

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for similar cached queries.

        Args:
            query: The query text to search for
            top_k: Number of results to return
            min_similarity: Minimum similarity threshold (default: use cache threshold)

        Returns:
            List of dictionaries containing cache entries with similarity scores
        """
        embedding = self._get_embedding(query)
        threshold = min_similarity if min_similarity is not None else self.similarity_threshold

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        id,
                        query_text,
                        response,
                        metadata,
                        hit_count,
                        created_at,
                        last_accessed_at,
                        1 - (query_embedding <=> %s::vector) AS similarity
                    FROM semantic_cache
                    WHERE query_embedding IS NOT NULL
                        AND 1 - (query_embedding <=> %s::vector) >= %s
                    ORDER BY query_embedding <=> %s::vector
                    LIMIT %s
                """, (embedding, embedding, threshold, embedding, top_k))

                results = []
                for row in cur.fetchall():
                    results.append({
                        'id': row['id'],
                        'query_text': row['query_text'],
                        'response': row['response'],
                        'metadata': row['metadata'],
                        'similarity': float(row['similarity']),
                        'hit_count': row['hit_count'],
                        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                        'last_accessed_at': row['last_accessed_at'].isoformat() if row['last_accessed_at'] else None
                    })
                return results
        finally:
            conn.close()

    def delete(self, cache_id: int) -> bool:
        """
        Delete a cache entry by ID.

        Args:
            cache_id: The ID of the cache entry to delete

        Returns:
            True if deleted, False if not found
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM semantic_cache WHERE id = %s", (cache_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def clear(self) -> int:
        """
        Clear all cache entries.

        Returns:
            Number of entries deleted
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM semantic_cache")
                count = cur.rowcount
                conn.commit()
                return count
        finally:
            conn.close()

    def stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) as total_entries,
                        SUM(hit_count) as total_hits,
                        AVG(hit_count) as avg_hits_per_entry,
                        MIN(created_at) as oldest_entry,
                        MAX(last_accessed_at) as most_recent_access
                    FROM semantic_cache
                """)
                result = cur.fetchone()

                return {
                    'total_entries': result['total_entries'],
                    'total_hits': result['total_hits'] or 0,
                    'avg_hits_per_entry': float(result['avg_hits_per_entry']) if result['avg_hits_per_entry'] else 0,
                    'oldest_entry': result['oldest_entry'].isoformat() if result['oldest_entry'] else None,
                    'most_recent_access': result['most_recent_access'].isoformat() if result['most_recent_access'] else None
                }
        finally:
            conn.close()


def create_cache_from_config(config_path: str = "lakebase_connection.json") -> SemanticCache:
    """
    Create a SemanticCache instance from a configuration file.

    The config file should contain:
    - profile: Databricks CLI profile name
    - endpoint: Full Lakebase endpoint path (projects/PROJECT_ID/branches/BRANCH/endpoints/primary)
    - database_name: Database name
    - similarity_threshold: Minimum cosine similarity for cache hits (0.0-1.0)
    - embedding_model: Databricks embedding model name

    Args:
        config_path: Path to the JSON configuration file (default: lakebase_connection.json)

    Returns:
        Initialized SemanticCache instance with connection to Lakebase

    Example config file:
        {
            "profile": "my-profile",
            "endpoint": "projects/my-project/branches/production/endpoints/primary",
            "database_name": "cache_db",
            "similarity_threshold": 0.85,
            "embedding_model": "databricks-bge-large-en"
        }
    """
    with open(config_path) as f:
        config = json.load(f)

    return SemanticCache(
        lakebase_profile=config['profile'],
        lakebase_endpoint=config['endpoint'],
        lakebase_database=config['database_name'],
        similarity_threshold=config['similarity_threshold'],
        embedding_model=config['embedding_model'],
    )

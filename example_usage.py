"""
Example usage of the Semantic Cache with Databricks Lakebase.

This demonstrates:
1. Creating cache entries
2. Retrieving cached responses with semantic matching
3. Searching for similar queries
4. Cache statistics
"""

from semantic_cache import create_cache_from_config


def main():
    print("🚀 Semantic Cache Demo\n")

    # Initialize cache from config file
    cache = create_cache_from_config("lakebase_connection.json")
    
    #clear
    cache.clear()

    # Example 1: Add cache entries
    print("📝 Adding cache entries...\n")

    queries_and_responses = [
        (
            "What is the capital of France?",
            "The capital of France is Paris.",
            {"source": "geography", "confidence": 1.0}
        ),
        (
            "How do I create a DataFrame in PySpark?",
            "You can create a DataFrame in PySpark using spark.createDataFrame() or spark.read methods.",
            {"source": "documentation", "language": "python"}
        ),
        (
            "What are the benefits of using Delta Lake?",
            "Delta Lake provides ACID transactions, time travel, schema enforcement, and unified batch/streaming.",
            {"source": "databricks_docs", "topic": "delta"}
        ),
        (
            "How to optimize a Spark job?",
            "To optimize Spark jobs: use partitioning, caching, broadcast joins, and adjust parallelism settings.",
            {"source": "best_practices", "difficulty": "intermediate"}
        ),
    ]

    for query, response, metadata in queries_and_responses:
        cache_id = cache.set(query, response, metadata)
        print(f"✅ Cached: \"{query[:50]}...\" (ID: {cache_id})")

    print("\n" + "="*80 + "\n")

    # Example 2: Retrieve with exact or similar queries
    print("🔍 Testing semantic matching...\n")

    test_queries = [
        "What's the capital city of France?",  # Similar to cached query
        "How can I make a PySpark DataFrame?",  # Similar wording
        "What advantages does Delta Lake offer?",  # Paraphrased
        "How to speed up my Spark application?",  # Related concept
        "What is the population of Tokyo?",  # Should NOT match
    ]

    for test_query in test_queries:
        result = cache.get(test_query, return_metadata=True)

        if result:
            response, similarity, metadata = result
            print(f"Query: \"{test_query}\"")
            print(f"✅ CACHE HIT (similarity: {similarity:.3f})")
            print(f"Response: \"{response[:80]}...\"")
            print(f"Metadata: {metadata}")
        else:
            print(f"Query: \"{test_query}\"")
            print(f"❌ CACHE MISS (no match above threshold)")

        print()

    print("="*80 + "\n")

    # Example 3: Search for similar queries
    print("🔎 Searching for similar queries to 'Delta Lake features'...\n")

    search_results = cache.search("Delta Lake features", top_k=3, min_similarity=0.7)

    for i, result in enumerate(search_results, 1):
        print(f"{i}. Query: \"{result['query_text']}\"")
        print(f"   Similarity: {result['similarity']:.3f}")
        print(f"   Response: \"{result['response'][:60]}...\"")
        print(f"   Hit count: {result['hit_count']}")
        print()

    print("="*80 + "\n")

    # Example 4: Cache statistics
    print("📊 Cache Statistics:\n")

    stats = cache.stats()
    print(f"Total entries: {stats['total_entries']}")
    print(f"Total hits: {stats['total_hits']}")
    print(f"Average hits per entry: {stats['avg_hits_per_entry']:.2f}")
    print(f"Oldest entry: {stats['oldest_entry']}")
    print(f"Most recent access: {stats['most_recent_access']}")

    print("\n✨ Demo complete!")


if __name__ == "__main__":
    main()

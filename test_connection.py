"""
Quick test script to verify Lakebase connection and pgvector extension.
"""

import json
import subprocess
import psycopg2


def test_connection():
    print("🧪 Testing Lakebase Connection\n")

    # Load config
    try:
        with open("lakebase_connection.json") as f:
            config = json.load(f)
        print("✅ Config file loaded")
    except FileNotFoundError:
        print("❌ Config file not found. Run setup_lakebase.sh first.")
        return

    # Get fresh token
    print("🔑 Generating OAuth token...")
    endpoint_path = f"projects/{config['project_id']}/branches/production/endpoints/primary"
    result = subprocess.run(
        ['databricks', 'postgres', 'generate-database-credential', endpoint_path,
         '--profile', config['profile'], '--output', 'json'],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"❌ Failed to generate token: {result.stderr}")
        return

    token = json.loads(result.stdout)['token']
    print("✅ Token generated")

    # Get user email
    result = subprocess.run(
        ['databricks', 'current-user', 'me',
         '--profile', config['profile'], '--output', 'json'],
        capture_output=True, text=True
    )
    email = json.loads(result.stdout)['userName']

    # Test connection
    print(f"🔌 Connecting to {config['host']}...")
    try:
        conn = psycopg2.connect(
            host=config['host'],
            port=5432,
            database=config['database_name'],
            user=email,
            password=token,
            sslmode='require'
        )
        print("✅ Connected successfully")

        # Test pgvector
        print("🧮 Testing pgvector extension...")
        with conn.cursor() as cur:
            cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';")
            result = cur.fetchone()
            if result:
                print(f"✅ pgvector extension installed (version {result[1]})")
            else:
                print("❌ pgvector extension not found")

        # Check table
        print("📊 Checking semantic_cache table...")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name = 'semantic_cache'
            """)
            if cur.fetchone()[0] == 1:
                cur.execute("SELECT COUNT(*) FROM semantic_cache")
                count = cur.fetchone()[0]
                print(f"✅ Table exists with {count} entries")
            else:
                print("❌ Table not found")

        conn.close()
        print("\n✨ All tests passed!")

    except psycopg2.Error as e:
        print(f"❌ Connection failed: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    test_connection()

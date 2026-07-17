import asyncio
import os
from pymilvus import AsyncMilvusClient, DataType
from dotenv import load_dotenv

load_dotenv()


async def main():
    uri = os.environ.get("MILVUS_URI", "http://localhost:19530")
    client = AsyncMilvusClient(uri=uri)

    collection_name = "test_diag"
    if await client.has_collection(collection_name):
        await client.drop_collection(collection_name)

    schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("version_id", DataType.VARCHAR, max_length=64)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=4)

    index_params = client.prepare_index_params()
    index_params.add_index("embedding", metric_type="COSINE")

    await client.create_collection(
        collection_name, schema=schema, index_params=index_params
    )
    await client.load_collection(collection_name)

    # Insert
    data = [
        {"version_id": "v1", "embedding": [0.1, 0.2, 0.3, 0.4]},
        {"version_id": "v2", "embedding": [0.5, 0.6, 0.7, 0.8]},
    ]
    await client.insert(collection_name, data=data)

    # Delete by version_id
    res = await client.delete(collection_name, filter='version_id == "v1"')
    print("Delete result by version_id:", res)

    # Query with Strong consistency immediately
    q_strong = await client.query(
        collection_name, filter="id >= 0", consistency_level="Strong"
    )
    print("Remaining chunks (Strong consistency):", q_strong)

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())

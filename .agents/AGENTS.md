# Project Customization Rules & Memory Guidelines

## 1. Architectural Style (Layered Architecture)
All features must be separated into strictly decoupled layers:
1. **API / Router Layer:** Handles endpoints, request payload schema validation, routing, HTTP statuses, and client authentication. No database queries or business operations are allowed here.
2. **Service Layer (Coordinating Service):** Implements business processes, coordinates database operations, caching, and document chunking.
3. **Repository / Adapter Layer:** Directly reads/writes to databases, search indexes, caches, and third-party tools (e.g. `MongoRepository`, `MilvusVectorStore`, `FAQRepository`, `MemoryRepository`).

---

## 2. Core Storage Engines
1. **Relational / Document Storage:** MongoDB using the async `motor` client. PostgreSQL/SQLite are deprecated.
2. **Vector Database:** Milvus (`pymilvus` `MilvusClient`).
3. **Caching & Long-term Indexing:** Redis for fast lookups.

---

## 3. Vector Index Constraints
1. **Index Type:** Strict **HNSW** index in Milvus.
2. **Metric Type:** **COSINE** similarity metric.
3. **Search Filters:** Filters results matching only **active** version IDs in the relational/document store.

---

## 4. Chat Session Memory Guidelines
1. **Memory Storage:** MongoDB or Redis based on caching performance.
2. **History Management:** Maintains short-term conversation context matching session UUIDs, passing it directly to prompt assemblies.

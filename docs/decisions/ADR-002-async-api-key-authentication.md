# ADR-002: Async API Key-Based Authentication and Centralized Configurations

## Status
Accepted

## Context
The authentication module was originally designed with stateless JWT access/refresh tokens. However, the chatbot APIs will be consumed directly by another backend service. In a backend-to-backend context:
1. Handling constant access token expirations and token refreshes adds significant logic complexity on the calling backend service side.
2. In stateless JWTs, role or permission updates take up to 15-30 minutes to propagate unless database checks are run on every request. This negates the performance benefit of stateless JWTs.
3. Synchronous cryptography operations (bcrypt hashing) block the FastAPI main event loop, restricting concurrency under load.

Therefore, we needed a simple, performant, and secure authentication mechanism tailored for backend service consumers.

## Options Considered
- **Option 1: Stateful JWTs with Redis lookup**: Decoded roles verified against a fast in-memory blacklist.
- **Option 2: Asynchronous API Key-based Authentication**: Random tokens generated, hashed using SHA-256, and verified asynchronously.

## Decision
We selected **Option 2: Asynchronous API Key-based Authentication**.

1. **API Keys**: Users generate labeled API keys (`sk_live_...`). On each request, the key is passed in the `X-API-Key` header.
2. **SHA-256 Hashing**: We hash the API key using SHA-256 for database comparison. Plaintext keys are never stored, protecting them against database leaks. Hashing with SHA-256 is extremely fast (< 1 microsecond), making it acceptable for per-request database validation.
3. **Async Database Engine**: Transited the database engine and sessions to fully asynchronous operations using `create_async_engine` (PostgreSQL `asyncpg` in production, SQLite `aiosqlite` in testing).
4. **Thread-Safe CPU Tasks**: CPU-bound cryptographic operations (bcrypt password hashing and secure token generation) are scheduled on background threads using `asyncio.to_thread` to prevent event loop blocks.
5. **Centralized Settings**: Centralized database and authentication configurations into the `app/configs` directory.

## Consequences
- **Pros**:
  - Extremely simple for client backend services to consume. No token refresh loops required.
  - Role modifications (RBAC changes) take effect instantly upon key validation.
  - Hashed storage ensures high security for generated keys.
  - Event loop remains completely unblocked during heavy cryptographic operations.
- **Cons**:
  - Requires a database lookup on every request to check key validity. However, database lookups are fast due to indexing on `key_hash`.

# OpenAI Rate Limit Issue

Date:
2026-07-14

Problem

429 Too Many Requests.

Symptoms

- Requests failed randomly
- Worker retries increased latency

Root Cause

Sending 100 concurrent requests.

Investigation

Checked logs.

Added request timestamps.

Found burst traffic.

Solution

Added asyncio.Semaphore(10)

Implemented exponential backoff.

Result

Success rate

83%
↓

99.9%

Lessons

Always limit outbound LLM concurrency.

References

PR #34
Commit: a83f212
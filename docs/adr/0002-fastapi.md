# 0002: FastAPI

## Decision
We will use FastAPI as the primary backend web framework. `backend/main.py` will serve as the explicit composition root.

## Why
High-frequency market data ingestion and trading operations require non-blocking I/O (asynchronous). FastAPI provides standard Python type hints out of the box, automatic documentation, and excellent performance for I/O bound tasks.

## Alternatives Considered
- Django: Too heavy, synchronous by default, and heavily couples business logic to the ORM.
- Flask: Lacks built-in async support and native type validation.

## Trade-offs
Async concurrency can be more complex to reason about and debug. Event loops must be carefully managed to avoid blocking operations from third-party sync libraries.

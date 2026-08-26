# 0003: Observation Layer Architecture

## Decision
The Market Data Service (observation layer) is designed with strictly isolated single-responsibility components: Exchange Client, Payload Normalizer, Event Publisher, and an Ingestion Use Case.

## Why
This enforces the rule that every module must have exactly one responsibility. An exchange client fetches data but doesn't parse it. A normalizer parses it but doesn't publish it. This prevents the "God Object" anti-pattern common in trading bots where a single massive class connects, parses, and executes logic.

## Alternatives Considered
- Fat Exchange Clients (where the client connects, normalizes, and publishes directly). Rejected because it violates the Single Responsibility Principle and makes unit testing impossible without mocking the network.

## Trade-offs
Increases the number of moving parts, interfaces, and DI wiring overhead, but vastly improves testability and interchangeability of components.

# 0001: Clean Architecture

## Decision
We will use Clean Architecture for the backend, strictly enforcing boundaries between Domain, Application, Infrastructure, and Presentation.

## Why
This project will eventually contain complex modules: market data, AI reasoning, execution, and learning. If these are not separated from day one, the project will become impossible to maintain. The Dependency Rule ensures that nothing in the domain knows about frameworks, external APIs, or databases.

## Alternatives Considered
- Standard MVC: Not suitable because it inherently couples business logic to the web framework and request/response cycle.
- Monolithic layered: Tends to blur boundaries over time, leading to tightly coupled "spaghetti" code.

## Trade-offs
Requires higher initial boilerplate. Developers must maintain strict discipline to avoid violating layer boundaries.

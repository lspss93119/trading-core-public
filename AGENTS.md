# Project Identity

`trading-core` is a reusable cryptocurrency trading infrastructure library.

It is not a single trading bot, scanner, dashboard, or trading strategy.

Its purpose is to provide reusable infrastructure shared by future projects such as:

* funding-rate scanners
* cross-exchange arbitrage scanners
* spot/perpetual basis scanners
* arbitrage bots
* market-making bots
* monitoring dashboards
* research tools
* automated execution systems

The primary architectural goal is:

> Integrate an exchange once and reuse that integration everywhere.

Adding a new exchange should normally be a local extension rather than requiring modifications across scanners, strategies, dashboards, and bots.

# Scope

The core library may eventually contain reusable capabilities such as:

* exchange abstractions
* exchange adapters
* normalized market-data models
* symbol/instrument normalization
* funding-rate normalization
* market-data collection primitives
* opportunity-analysis primitives
* execution primitives
* shared risk primitives

Application-specific functionality should normally live outside this package.

Examples of functionality that should NOT automatically become part of `trading-core`:

* web dashboards
* frontend UI
* application-specific alerting
* one-off research scripts
* strategy-specific presentation code
* TradingView integrations
* application deployment configuration

Only move functionality into `trading-core` when it represents a genuinely reusable domain capability.

# Exchange Independence

Higher-level business logic must not depend directly on a specific exchange API.

Strategies, scanners, and opportunity finders must not directly import or depend on:

* CCXT exchange clients
* exchange SDK clients
* Hummingbot connector objects
* raw REST responses
* raw WebSocket messages

Third-party and exchange-specific behavior belongs behind an internal exchange adapter boundary.

# Integration Preference

When adding exchange support, prefer this order:

1. Existing reusable integration already present in `trading-core`
2. CCXT / CCXT Pro support
3. Small exchange-specific override around the CCXT adapter
4. Official exchange SDK
5. Native REST / WebSocket implementation

Do not rebuild functionality that a sufficiently reliable existing integration already provides.

However, do not force CCXT when a native integration provides materially better functionality, correctness, latency, or access to exchange-specific features.

# Partial Exchange Support Is Valid

An exchange does not need to support every capability.

For example, a newly launched exchange may initially support only:

* markets
* ticker
* order book
* funding rate
* mark price
* index price

Such an exchange must still be usable by monitoring and opportunity-discovery tools.

Do not require trading functionality merely to register an exchange.

Capabilities must eventually be explicit and discoverable.

# Normalization Boundary

Exchange-specific formats must be normalized before leaving the exchange integration layer.

Examples include:

* symbols
* instruments
* timestamps
* funding intervals
* prices
* quantities
* order states
* position states
* error types

Avoid leaking raw exchange dictionaries into higher-level modules.

# Financial Correctness

Use `Decimal` where precision matters for:

* price
* quantity
* fees
* funding rates
* PnL
* financial calculations

Be explicit about:

* units
* timestamps
* timezone assumptions
* funding intervals
* percentage vs decimal representation

Never silently compare funding rates with different settlement intervals without normalization.

# Async Architecture

Network-heavy exchange interfaces should be async-first where appropriate.

The architecture should eventually support concurrently monitoring many exchanges and markets without requiring application code to manage exchange-specific concurrency details.

Do not introduce unnecessary concurrency complexity before it is needed.

# Execution Safety

Market-data support and live-trading support are separate capabilities.

Adding an exchange for monitoring must never silently enable trading.

Live execution functionality must eventually account for:

* unknown order state after timeout
* partial fills
* stale order state
* position reconciliation
* retry safety
* leg risk
* emergency flattening
* rate limits
* reconnect behavior

Do not assume that a timed-out order request means the exchange rejected the order.

# Architecture Principles

Prefer:

* small focused modules
* explicit interfaces
* dependency inversion
* composition
* typed domain models
* testability
* replaceable third-party integrations
* stable public APIs
* incremental implementation

Avoid:

* giant exchange classes
* God objects
* duplicated exchange API code
* duplicated symbol normalization
* strategy-specific logic inside exchange connectors
* exchange-specific `if/elif` branches scattered throughout the project
* unnecessary inheritance trees
* premature microservices
* premature distributed systems

# Development Strategy

Build this project incrementally.

A typical progression for a new exchange should be:

Stage 1 — Discovery / Market Data

* markets
* ticker
* funding
* order book
* mark/index price

Stage 2 — Research

* funding history
* open interest
* volume
* fees
* trading rules
* metadata

Stage 3 — Execution

* authentication
* balances
* positions
* place order
* cancel order
* order status

Stage 4 — Production Reliability

* private streams
* reconnect
* reconciliation
* recovery
* advanced rate-limit handling
* execution safety

Only implement the stages required by the current use case.

# Testing

Reusable exchange integrations and financial normalization logic require tests.

Eventually prefer:

* unit tests
* exchange adapter contract tests
* mock exchange implementations
* normalization tests
* regression tests
* tests that do not require real funds

Tests and examples must not submit real trades by default.

# Public API Discipline

Before exposing a new object or function as part of the public package API, consider whether downstream projects should depend on it long term.

Prefer a small, understandable public API.

Internal implementation details may change without forcing consumers to change.

# AI / Codex Workflow

When working on this repository:

1. Read this `AGENTS.md`.
2. Inspect existing architecture before adding new abstractions.
3. Reuse existing domain models and interfaces.
4. Avoid creating parallel implementations of capabilities that already exist.
5. For significant architecture changes, explain the proposed change before implementing it.
6. Keep new exchange integrations isolated.
7. Add or update tests for reusable behavior.
8. Run relevant verification before claiming completion.

# Core Principle

The success criterion for this repository is:

> New tools should reuse `trading-core`, and new exchanges should be integrated once.

If adding a new exchange or new application requires repeatedly rewriting exchange connectivity, normalization, or common trading-domain logic, reconsider the architecture.

# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React 19, Vite 8, Tailwind CSS 4, TanStack React Query, React Router, React Hook Form, Zod, and Radix UI.

## Users

Travellers who submit a trip brief, observe the real pipeline's progress, and inspect itinerary evidence item by item.

## Product Purpose

Periplus turns a traveller's brief into an itinerary and related content while retaining the evidence and verification outcome behind each factual statement. Success means the traveller can follow the actual pipeline state and audit the resulting plan line by line rather than relying on untraceable prose.

## Positioning

Every factual claim is bound to evidence, then assessed in an independent verification stage that records a verdict. The agent that finds a fact does not approve its own work.

## Operating Context

A trip moves through exactly four stages, in order: `research` / `verify` / `plan` / `write`. The traveller submits the brief, monitors stage progress, and reviews the completed itinerary together with its claims, evidence, and verdicts.

## Capabilities and Constraints

- Preserve the existing backend API and generated type contracts.
- Never fabricate facts, success rates, or estimated completion times.
- Tests must be deterministic and must not contact real APIs, search providers, maps, or models.
- Treat unsupported, stale, and contradicted claims as explicit outcomes rather than silently dropping them.

## Brand Commitments

The user has fixed the direction as a “modern sailing log”: ivory paper, ink black, and oxidized bronze. Green is reserved for `supported`; vermilion is reserved for `contradicted` and failure. This commitment deliberately does not define concrete design tokens.

## Evidence on Hand

- The checked-in generated API schema defines the current web/backend contract.
- Existing architecture and domain documentation define claim, evidence, verdict, and stage semantics.
- No success-rate, ETA, testimonial, or production-readiness evidence is available; future work must not invent it.

## Product Principles

- Provenance is part of the product, not an afterthought.
- Verification remains independent from research.
- Pipeline state shown to travellers reflects real run state.
- Uncertainty and contradiction stay visible.
- Deterministic typed boundaries keep the system testable without external services.

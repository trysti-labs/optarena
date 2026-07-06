# OptArena Benchmark Corpus Specification

## Purpose

This document defines the benchmark strategy for OptArena. The benchmark
corpus is intended to become the project's primary long-term moat by
providing realistic, reproducible software engineering tasks.

## Design Principles

-   Repository-based benchmarks
-   Hidden tests
-   Docker verification
-   Real engineering tasks
-   Polyglot
-   Framework aware
-   Reproducible
-   Community extensible

# Benchmark Metadata

Every benchmark should contain: - Benchmark ID - Name - Description -
Language - Framework - Domain - Repository size - Difficulty - Task
type - Prompt - Starter repository - Hidden tests - Docker image -
Verification command - Tags - Estimated runtime

# Difficulty

## Level 1 - Easy

1-5 files - Simple feature - Small bug - Unit tests - Config change

## Level 2 - Beginner

5-20 files - CRUD - Logging - Validation - Refactoring - Docker fixes

## Level 3 - Intermediate

20-100 files - Authentication - Async - API integration - DB migration -
React feature

## Level 4 - Advanced

100-500 files - Multi-module feature - Security fixes - Architecture -
Performance

## Level 5 - Expert

500+ files - Enterprise repositories - Framework migrations -
Distributed systems - Large refactors

# Task Categories

-   Feature Development
-   Bug Fixing
-   Refactoring
-   Testing
-   Security
-   Performance
-   DevOps
-   Data Engineering
-   Documentation
-   Dependency Upgrades

# Domains

-   Backend
-   Frontend
-   Mobile
-   Desktop
-   Infrastructure
-   DevOps
-   Data Engineering
-   Machine Learning
-   Security
-   Embedded

# Repository Sizes

-   Tiny
-   Small
-   Medium
-   Large
-   Enterprise

# Phase 1 (Launch)

Target: 100-150 benchmarks

Python (20) - FastAPI - Django - Flask - SQLAlchemy - Pydantic - Typer

JavaScript / TypeScript (20) - Node - Express - NestJS - React -
Next.js - Vue - Angular

Java (15) - Spring Boot

Go (10) - Gin - Fiber

Rust (10) - Axum - Actix

C# (10) - ASP.NET Core

C/C++ (10)

SQL (10)

Shell (5)

Docker / Compose (5)

Terraform (5)

# Phase 2

Target: 400-600 benchmarks

Add: - Kotlin - Swift - Dart - Flutter - Android - iOS - PHP - Laravel -
Ruby on Rails - Scala - Micronaut - Quarkus - Phoenix - Svelte

Increase repository complexity.

# Phase 3

Target: 2000+ benchmarks

20+ languages 50+ frameworks 500+ repository templates 10+ engineering
domains

# Distribution

Per language:

20% Feature work

20% Bug fixes

15% Refactoring

15% Testing

10% Security

10% Performance

10% DevOps

# Hidden Verification

Every benchmark includes: - Prompt - Starter repository - Hidden tests -
Docker image - Verification command

Verification uses: - Compilation - Unit tests - Integration tests -
Lint - Static analysis - Type checking

Never keyword matching alone.

# Tags

Examples: authentication oauth jwt graphql docker async filesystem react
fastapi spring terraform kubernetes performance sql caching

# Benchmark Suites

Arena Lite - 20 cases - Under 5 minutes

Arena Standard - 100 cases - \~20 minutes

Arena Extended - 500 cases - \~2 hours

Arena Enterprise - Entire corpus - Overnight execution

# Community Contribution

Benchmark authors provide: - Repository - Prompt - Hidden tests - Docker
image - Metadata

OptArena scaffolds the remaining files.

# Success Criteria

The benchmark corpus should: - Cover major languages - Cover popular
frameworks - Reflect real engineering work - Support enterprise
regression testing - Be reproducible - Encourage community
contributions - Become the standard benchmark corpus for AI software
engineering agents.

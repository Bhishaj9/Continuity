# Project Continuity

A resilient, distributed SaaS for AI-driven video bridge generation using Veo 3.1 and Gemini 2.0.

## Key Features

- **Distributed Architecture**: Decoupled FastAPI producer and Redis-backed background workers.
- **Fault Tolerance**: Redis `BRPOPLPUSH` persistence for zero-job-loss recovery.
- **Financial Integrity**: Idempotent billing system with database-level row locking.
- **Production Orchestration**: Dockerized environment with automated backups and healthchecks.

## The Stack

- **AI**: Google Veo 3.1 (Video Generation) & Gemini 2.0 (Agentic Control).
- **Backend**: Python, FastAPI, PostgreSQL.
- **Infra**: Redis, Docker, public.ecr.aws images.
- **Library**: Powered by the open-source continuity-stitch engine.

## Operational Guide

Key Makefile commands for system management:

```bash
make deploy
make update
make backup
```

## Project Status

**v1.0.0-Beta (Production-Hardened)**

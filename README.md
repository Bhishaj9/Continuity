# Project Continuity: Cinematic AI Video Bridging
![License](https://img.shields.io/badge/License-Proprietary-red.svg)

**Production-Grade SaaS for Autonomous Video Post-Production**

Continuity is an agentic workflow that acts as an autonomous professional film crew in your browser. It automatically generates seamless "bridge" transitions between two disparate video clips using a coordinated multi-agent system.

By orchestrating Gemini 2.0 Flash (Analysis/Director) and Google Veo 3.1 (Cinematographer/Generator), Continuity understands the visual semantics of your footage and hallucinates a physics-defying, narratively consistent transition to stitch them together.

![System Architecture](placeholder_architecture.png)
*[Demo Video](placeholder_video_link)*

## 🏗️ Technical Architecture

Continuity is built as a scalable, cloud-native application designed for high availability and fault tolerance.

*   **Backend**: [FastAPI](https://fastapi.tiangolo.com/) provides a high-performance, asynchronous web server.
*   **Database**: [SQLAlchemy](https://www.sqlalchemy.org/) manages persistence with atomic transactions and optimistic locking to prevent race conditions.
*   **AI Core**:
    *   **Director**: Gemini 2.0 Flash for semantic analysis and prompt engineering.
    *   **Cinematographer**: Google Veo 3.1 on Vertex AI for high-fidelity video generation.
*   **Payments**: [Stripe](https://stripe.com/) integration for secure credit management, webhooks, and billing.
*   **Infrastructure**: Fully containerized with [Docker](https://www.docker.com/) for consistent deployment across environments.

## 📈 Scalability

Continuity is designed to scale with your production needs.

*   **V1.0 (Current)**: Uses an in-memory `asyncio` queue for simplicity and ease of deployment. Best suited for single-instance deployments.
*   **V2.0 (Roadmap)**: Will migrate to **Redis** for the job queue, allowing for horizontal scaling of worker nodes and robust persistence of job states across restarts.

## ✨ Key Features

*   **💰 Stripe-Backed Credit System**: Robust billing integration allowing users to purchase credits, with secure balance tracking and automated refunds for failed jobs.
*   **⚛️ Atomic Job Processing**: Advanced job queue utilizing row-level locking and optimistic concurrency control to ensure data integrity during parallel processing.
*   **🔐 Google OAuth2 Security**: Enterprise-grade authentication using Google Sign-In with strict JWT verification for secure user access.
*   **🎬 Automated Direction**: The Analyst Agent watches your clips to understand lighting, motion, and subject matter, drafting a precise VFX prompt.
*   **⚡ Fault-Tolerant Polling**: Implements a "Dead Man's Switch" mechanism to handle long-running generation tasks (10m+) without hanging.
*   **🎨 Glass UI Dashboard**: A responsive, dark-mode "Director's Dashboard" for managing projects, viewing history, and controlling generation parameters.

## 🚀 Installation & Quick Start

### Prerequisites
- Python 3.10+
- FFmpeg installed and added to system PATH.
- Google Cloud Project with Vertex AI API enabled.
- Stripe Account (for billing features).

> **Important**: While the app supports SQLite for development, **PostgreSQL is required for production** to support the `with_for_update()` row-level locking correctly.

### 1. Clone the Repository
```bash
git clone https://github.com/Bhishaj9/Continuity.git
cd continuity
```

### 2. Environment Configuration
Create a `.env` file in the root directory using `.env.example` as a template.

```ini
# Core AI Configuration
GOOGLE_API_KEY=your_gemini_api_key
GCP_PROJECT_ID=your_gcp_project_id
GCP_LOCATION=us-central1

# Stripe Configuration
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
PRICE_PER_CREDIT=10
COST_PER_JOB=5

# Google OAuth Configuration
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

### 3. Install Dependencies
Use the Makefile to install all required dependencies:

```bash
make install
```

### 4. Run Locally
Start the server and dashboard:

```bash
python server.py
```
Access the dashboard at http://localhost:7860.

## 🐳 Docker Deployment

Continuity is optimized for containerized environments.

Build the Docker image:

```bash
make docker-build
```

Run the container:

```bash
docker run -p 7860:7860 --env-file .env continuity-app
```

## ⚖️ License & Rights

**Copyright (c) 2026 Bhishaj. All Rights Reserved.**

This software is **Proprietary**.
*   The source code is available for inspection and educational purposes only.
*   Unauthorized copying, modification, distribution, or commercial use is strictly prohibited without a license.
*   See the [LICENSE](LICENSE) file for full details.

Contact gaurav.vashistha09@gmail.com for licensing inquiries.

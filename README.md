# Continuity 🎬
![License](https://img.shields.io/badge/License-Proprietary-red.svg)

AI-Powered Video Bridging & Post-Production Agent

Continuity is an agentic workflow that acts as an autonomous professional film crew in your browser. It automatically generates seamless "bridge" transitions between two disparate video clips using a coordinated multi-agent system.

By orchestrating Gemini 2.0 Flash (Analysis/Director) and Google Veo 3.1 (Cinematographer/Generator), Continuity understands the visual semantics of your footage and hallucinates a physics-defying, narratively consistent transition to stitch them together.

## ✨ Key Features
**🎬 Automated Direction**: The Analyst Agent (Gemini 2.0 Flash) watches your clips to understand lighting, motion, and subject matter, drafting a precise VFX prompt.

**🤖 Generative Cinematography**: The Producer Agent (Veo 3.1) executes the vision, generating high-fidelity transition videos.

**⚡ Fault-Tolerant Architecture**: Implements a robust "Dead Man's Switch" polling mechanism and Type-Safe SDK proxies to ensure long-running video generation jobs (10m+ timeouts) do not crash or hang.

**🧵 Smart Stitching**: Integrated FFmpeg pipeline automatically normalizes framerates/resolutions and stitches the final Clip A + Bridge + Clip C sequence.

**🎨 Glass UI Dashboard**: A responsive, dark-mode "Director's Dashboard" featuring history galleries, advanced physics controls (motion strength, guidance scale), and real-time status updates.

**☁️ Cloud Native**: Built for Docker and Hugging Face Spaces, with optional Google Cloud Storage (GCS) persistence for video history.

## 🛠️ Tech Stack
**Frontend**: HTML5, Tailwind CSS (Glassmorphism design), Vanilla JS.

**Backend**: Python 3.10, FastAPI, Uvicorn.

**AI Orchestration**: Google GenAI SDK (google-genai), Vertex AI (veo-3.1-generate-preview).

**Video Processing**: FFmpeg (via subprocess), OpenCV.

**Infrastructure**: Docker, Google Cloud Platform (Vertex AI, Cloud Storage).

## 🚀 Installation & Setup

### Prerequisites
- Python 3.10+
- FFmpeg installed and added to system PATH.
- Google Cloud Project with Vertex AI API enabled.

### 1. Clone the Repository
```bash
git clone https://github.com/Bhishaj9/Continuity.git
cd continuity
```

### 2. Environment Configuration
Create a `.env` file in the root directory. You can use `.env.example` as a template.

```ini
# Required for Director Node (Gemini)
GOOGLE_API_KEY=your_gemini_api_key

# Required for Generator Node (Veo 3.1 on Vertex AI)
GCP_PROJECT_ID=your_gcp_project_id
GCP_LOCATION=us-central1

# Optional: For Cloud Storage Persistence
GCP_BUCKET_NAME=your_gcp_bucket_name
GCP_CREDENTIALS_JSON={"type": "service_account", ...} 
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run Locally
Start the FastAPI server:

```bash
python Continuity/server.py
```
Access the dashboard at http://localhost:7860.

## 🐳 Docker Deployment
Continuity is optimized for containerized environments (like Hugging Face Spaces).

Build the image:

```bash
docker build -t continuity-app -f Continuity/Dockerfile .
```
Run the container:

```bash
docker run -p 7860:7860 --env-file Continuity/.env continuity-app
```

## 📖 Usage Guide
**Ingest**: Drag and drop your Start Clip (Scene A) and End Clip (Scene C) into the dashboard.

**Analyze**: Click "Analyze Scenes". The Director Agent will inspect both clips and generate a creative transition prompt.

**Refine**: Review the "Director's Configuration" panel. You can tweak:

- **Visual Style**: Cinematic, Cyberpunk, Anime, etc.
- **Physics**: Adjust Motion Strength (1-10) and Guidance Scale (1-20).
- **Negative Prompts**: Remove unwanted elements like "blur" or "text".

**Generate**: Click "Generate Video". The system will queue the job, poll Vertex AI for completion, and stitch the results.

**Export**: Once complete, preview the result in the "Bridge" player or download the full stitched sequence.

## 🧩 Architecture Overview
The system uses a JobQueue pattern to handle long-running video generation tasks asynchronously.

`/analyze` **Endpoint**: Uploads clips -> Hashes files (deduplication) -> Sends to Gemini 2.0 Flash for semantic analysis.

`/generate` **Endpoint**: Pushes a task to the background queue -> Initializes Veo 3.1 generation -> Returns a Job ID.

**Polling Loop** (`agent.py`):

- Uses a robust while loop with type-safe SDK calls (`types.GenerateVideosOperation`) to check job status.
- Includes a "Dead Man's Switch" to prevent infinite hanging if the API goes silent.

**Stitching** (`utils.py`):

- Normalizes videos to 1080p @ 24fps.
- Concatenates A -> Bridge -> C using FFmpeg.

## 🤝 Contributing
Contributions are welcome! Please ensure any PRs affecting the generation logic include updated unit tests in `Continuity/tests/`.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License
> ⚠️ **Proprietary Software**: The source code is available for inspection and educational purposes only. Unauthorized copying, modification, distribution, or commercial use is strictly prohibited without a license. Contact bhishaj9@gmail.com for licensing inquiries.
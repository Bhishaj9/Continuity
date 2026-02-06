import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Settings:
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
    GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
    GCP_CREDENTIALS_JSON = os.getenv("GCP_CREDENTIALS_JSON")
    HF_TOKEN = os.getenv("HF_TOKEN")
    GCP_BUCKET_NAME = os.getenv("GCP_BUCKET_NAME")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

    @classmethod
    def setup_auth(cls):
        """Sets up Google Application Credentials if JSON is provided in env."""
        if cls.GCP_CREDENTIALS_JSON:
            print("🔐 Found GCP Credentials Secret. Setting up auth...")
            creds_path = "gcp_credentials.json"
            with open(creds_path, "w") as f:
                f.write(cls.GCP_CREDENTIALS_JSON)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

    @classmethod
    def validate(cls):
        """Validates critical environment variables."""
        if not cls.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is missing from environment variables.")
        if not cls.HF_TOKEN:
             print("⚠️ HF_TOKEN is missing. Audio generation may fail.")
        if not cls.GCP_BUCKET_NAME:
             print("⚠️ GCP_BUCKET_NAME is missing. Cloud persistence will be disabled.")

# Run setup and validation immediately on import
Settings.setup_auth()
Settings.validate()

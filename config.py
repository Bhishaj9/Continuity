import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Settings:
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
    GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
    GCP_CREDENTIALS_JSON = os.getenv("GCP_CREDENTIALS_JSON")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")

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

# Run setup and validation immediately on import
Settings.setup_auth()
Settings.validate()

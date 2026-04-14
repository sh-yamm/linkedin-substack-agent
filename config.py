import os
from dotenv import load_dotenv

load_dotenv()

# Mistral
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")

# Substack
SUBSTACK_PUBLICATION_URL = os.getenv("SUBSTACK_PUBLICATION_URL", "").rstrip("/")
SUBSTACK_SID = os.getenv("SUBSTACK_SID", "")
SUBSTACK_LLI = os.getenv("SUBSTACK_LLI", "")

# Email
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")

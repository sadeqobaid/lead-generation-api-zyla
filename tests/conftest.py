import os


os.environ.setdefault("DATABASE_URL", "sqlite:///./test_lead_generation.db")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-sufficient-entropy")
os.environ.setdefault("API_KEY_PEPPER", "test-api-key-pepper")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "60")
os.environ.setdefault("DEFAULT_CREDITS", "100")
os.environ.setdefault("ZYLA_ENABLED", "true")
os.environ.setdefault("ZYLA_AUTH_MODE", "shared_token")
os.environ.setdefault("ZYLA_SHARED_TOKEN", "test-zyla-shared-token-with-sufficient-entropy")
os.environ.setdefault("ZYLA_TENANT_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("ZYLA_TENANT_SLUG", "zyla-marketplace")
os.environ.setdefault("ZYLA_ALLOW_SYNTHETIC", "true")
os.environ.setdefault("ZYLA_DEFAULT_CREDITS", "1000000")
os.environ.setdefault("ZYLA_MAX_LIMIT", "25")

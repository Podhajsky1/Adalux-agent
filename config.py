from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""          # Czech +420... number from Twilio
    ANTHROPIC_API_KEY: str = ""
    BASE_URL: str = "https://your-app.railway.app"  # Railway public URL
    GOOGLE_CALENDAR_CREDENTIALS: str = "" # Service account JSON as string
    CALENDAR_ID: str = "primary"
    EXCEL_LOG_FILE: str = "data/call_log.xlsx"
    MAX_TURNS: int = 14                    # max conversation turns before polite exit
    CALL_DELAY_SEC: int = 45              # pause between calls in a campaign
    HAIKU_MODEL: str = "claude-haiku-4-5-20251001"  # model used for the live call

    class Config:
        env_file = ".env"

settings = Settings()

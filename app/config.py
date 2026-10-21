import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SECRET_KEY: str = "CHANGE_THIS_IN_PRODUCTION"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "CHANGE_THIS_PASSWORD"

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    DATABASE_URL: str = "sqlite:///./data/dkcraftbbq.db"
    UPLOAD_DIR: str = "./data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 5

    ALLOWED_ORIGINS: str = "http://localhost:8000"

    WEBHOOK_SECRET: str = ""
    SERVICE_NAME: str = "dkcraftbbq.service"
    # Skip analytics for the home network (the site's own public IP, LAN, localhost)
    EXCLUDE_HOME_NETWORK: bool = True
    EXCLUDE_IPS: str = ""  # extra comma-separated addresses to skip
    # TikTok sync (app/tiktok_sync.py)
    TIKTOK_USERNAME: str = "dkcraftbbq"
    YTDLP_PATH: str = "yt-dlp"
    FFMPEG_PATH: str = "ffmpeg"
    FFPROBE_PATH: str = "ffprobe"


    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def get_allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()

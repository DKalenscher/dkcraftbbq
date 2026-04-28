import pathlib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Absolute path — DB location never depends on working directory
BASE_DIR = pathlib.Path(__file__).parent.parent.resolve()
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "uploads").mkdir(exist_ok=True)

_DB_URL = f"sqlite:///{DATA_DIR / 'dkcraftbbq.db'}"
_UPLOAD_DIR = str(DATA_DIR / "uploads")

engine = create_engine(
    _DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

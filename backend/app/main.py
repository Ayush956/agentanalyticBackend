from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import get_database, ping_database
from app.postgres import ping_postgres
from app.routers import analytics, auth, chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    ping_database()
    ping_postgres()
    yield


app = FastAPI(lifespan=lifespan)
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(chat.router)


@app.get("/")
def hello():
    return {"message": "Hello World"}


@app.get("/api/hello")
def api_hello():
    return {"message": "Hello World"}


@app.get("/api/health")
def health():
    ping_database()
    db = get_database()
    return {
        "status": "ok",
        "database": settings.mongodb_db_name,
        "tickets": db.tickets.count_documents({}),
        "seed_applied": db.seed_meta.find_one({"_id": "tickets_seed_v1"}) is not None,
    }

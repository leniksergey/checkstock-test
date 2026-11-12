from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import connect, init_db
from . import stock, transfers, fbs, fbo, warehouses, history


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CheckStock", lifespan=lifespan)

app.include_router(stock.router)
app.include_router(transfers.router)
app.include_router(fbs.router)
app.include_router(fbo.router)
app.include_router(warehouses.router)
app.include_router(history.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def read_root(request: Request, tab: str = "stock"):
    active_tab = tab if tab in ("stock", "warehouses", "history") else "stock"
    return templates.TemplateResponse("index.html", {"request": request, "active_tab": active_tab})


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}

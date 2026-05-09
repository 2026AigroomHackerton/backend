from fastapi import FastAPI

app = FastAPI(
    title="AI Mobile Document Assistant API",
    version="0.1.0",
)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"status": "ok", "service": "AI Mobile Document Assistant API"}

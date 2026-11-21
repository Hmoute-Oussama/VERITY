from fastapi import FastAPI

app = FastAPI()

@app.post("/login")
def login():
    return {"ok": True}

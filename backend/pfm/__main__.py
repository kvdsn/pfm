import uvicorn

uvicorn.run("pfm.server:create_app", factory=True, host="127.0.0.1", port=8000, reload=False)

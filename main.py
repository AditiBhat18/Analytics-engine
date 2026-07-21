from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import List
import json

app = FastAPI(title="Real-time Analytics Engine")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, data: dict):
        # Send structured JSON data to ALL connected users
        for connection in self.active_connections:
            await connection.send_json(data)

manager = ConnectionManager()

@app.get("/")
def read_root():
    return {"status": "Analytics Engine Online"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Receive incoming data formatted as JSON
            data = await websocket.receive_json()
            
            # Print to server logs for debugging
            print(f"Received Event: {data}")
            
            # Add server processing timestamp / metadata wrapper
            payload = {
                "event_type": data.get("type", "UNKNOWN_EVENT"),
                "payload": data.get("payload", {}),
                "status": "processed"
            }
            
            # Broadcast structured payload to all connected clients
            await manager.broadcast_json(payload)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast_json({"event_type": "USER_DISCONNECTED"})
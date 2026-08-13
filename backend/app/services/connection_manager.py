from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[session_id] = websocket

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)

    def is_connected(self, session_id: str) -> bool:
        return session_id in self._connections

    async def send_json(self, session_id: str, payload: dict) -> bool:
        websocket = self._connections.get(session_id)
        if websocket is None:
            return False

        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            self.disconnect(session_id)
            return False


manager = ConnectionManager()

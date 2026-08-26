import asyncio
import json

import websockets


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8000/ws/market") as ws:
        for i in range(3):
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            d = json.loads(msg)
            print(f"WS market #{i + 1}: price={d.get('price')}")
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        d = json.loads(msg)
        print(f"WS main: equity={d.get('equity')} supervisor={d.get('supervisor')}")


asyncio.run(main())

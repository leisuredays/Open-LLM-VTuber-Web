"""
A2F WebSocket Bridge (Linux side)
- Receives processed A2F frames from Windows via HTTP POST
- Streams them to browser clients via WebSocket at real-time rate

Windows bridge POSTs: POST /frames  body: {"fps": 30, "frames": [{"t":..., "params":{...}}, ...]}
Browser connects: ws://localhost:9870
"""

import asyncio
import json
import sys

try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("pip install websockets")
    sys.exit(1)

FPS = 30
connected_clients = set()

async def ws_handler(websocket):
    connected_clients.add(websocket)
    print(f"[ws] Client connected ({len(connected_clients)} total)")
    try:
        async for msg in websocket:
            pass  # clients don't send anything meaningful
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(websocket)
        print(f"[ws] Client disconnected ({len(connected_clients)} total)")


async def stream_frames(frames, fps=30):
    """Stream pre-mapped frames to all WS clients at real-time rate."""
    if not connected_clients:
        print("[stream] No clients connected, skipping")
        return

    frame_interval = 1.0 / fps
    import time
    start_time = time.time()

    for i, frame in enumerate(frames):
        msg = json.dumps(frame)
        await asyncio.gather(
            *[client.send(msg) for client in connected_clients],
            return_exceptions=True
        )
        target_time = start_time + (i + 1) * frame_interval
        delay = target_time - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

    end_msg = json.dumps({"end": True, "total_frames": len(frames)})
    await asyncio.gather(
        *[client.send(end_msg) for client in connected_clients],
        return_exceptions=True
    )
    print(f"[stream] Done: {len(frames)} frames to {len(connected_clients)} clients")


async def http_handler(reader, writer):
    """Receive frames from Windows bridge via HTTP POST."""
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=10)
        request = request_line.decode().strip()
        headers = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            key, _, value = line.decode().partition(":")
            headers[key.strip().lower()] = value.strip()

        if "POST" in request and "/status" in request:
            content_length = int(headers.get("content-length", 0))
            body = await asyncio.wait_for(reader.readexactly(content_length), timeout=10) if content_length > 0 else b"{}"
            data = json.loads(body)
            status_msg = json.dumps({"status": data.get("text", ""), "type": "status"})
            await asyncio.gather(
                *[client.send(status_msg) for client in connected_clients],
                return_exceptions=True
            )
            print(f"[status] Broadcast: {data.get('text', '')}")
            response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n\r\n{{\"ok\":true}}\r\n"

        elif "POST" in request and "/frames" in request:
            content_length = int(headers.get("content-length", 0))
            if content_length > 0:
                body = await asyncio.wait_for(reader.readexactly(content_length), timeout=60)
                data = json.loads(body)
                frames = data.get("frames", [])
                fps = data.get("fps", FPS)
                print(f"[http] Received {len(frames)} frames, streaming...")
                # Stream in background so HTTP returns immediately
                asyncio.create_task(stream_frames(frames, fps))
                response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n\r\n{{\"status\":\"streaming\",\"frames\":{len(frames)}}}\r\n"
            else:
                response = "HTTP/1.1 400 Bad Request\r\n\r\n{\"error\":\"no body\"}\r\n"
        elif "GET" in request:
            response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{{\"clients\":{len(connected_clients)}}}\r\n"
        else:
            response = "HTTP/1.1 404 Not Found\r\n\r\n{}\r\n"

        writer.write(response.encode())
        await writer.drain()
    except Exception as e:
        print(f"[http] Error: {e}", file=sys.stderr)
    finally:
        writer.close()


async def main():
    ws_port = 9870
    http_port = 9871

    print(f"A2F WebSocket Bridge (Linux)")
    print(f"  WS:   ws://0.0.0.0:{ws_port}")
    print(f"  HTTP: http://0.0.0.0:{http_port}/frames")

    ws_server = await serve(ws_handler, "0.0.0.0", ws_port)
    http_server = await asyncio.start_server(http_handler, "0.0.0.0", http_port)

    print("Ready!")
    await asyncio.gather(ws_server.serve_forever(), http_server.serve_forever())

if __name__ == "__main__":
    asyncio.run(main())

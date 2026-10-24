"""真实 Uvicorn + TCP 升级与断线回收；TestClient 不能覆盖协议依赖和服务关停。"""
from __future__ import annotations

import json
import socket
import threading
import time

import uvicorn
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.web.app import create_app
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, CloseConnection, RejectConnection, Request, TextMessage


def test_real_websocket_stream_updates_and_disconnect_releases_server(tmp_path):
    context = build_context(load_settings({"DB_PATH": str(tmp_path / "toy.db")}), simulate=True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        create_app(context), lifespan="off", log_level="warning",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    graceful = False
    try:
        deadline = time.monotonic() + 3
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert server.started, "真实 Uvicorn 未启动"
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.settimeout(2)
            ws = WSConnection(ConnectionType.CLIENT)
            client.sendall(ws.send(Request(host=f"127.0.0.1:{port}", target="/api/v1/events")))
            accepted, chunks = False, []

            def receive_state():
                nonlocal accepted
                while True:
                    data = client.recv(65536)
                    assert data, "WebSocket 在状态帧前关闭"
                    ws.receive_data(data)
                    for event in ws.events():
                        assert not isinstance(event, RejectConnection), f"升级失败：{event}"
                        if isinstance(event, AcceptConnection):
                            accepted = True
                        elif isinstance(event, TextMessage):
                            chunks.append(event.data)
                            if event.message_finished:
                                frame = json.loads("".join(chunks))
                                chunks.clear()
                                assert accepted and frame["kind"] == "STATUS"
                                return frame["state"]

            assert receive_state() == "IDLE"
            context.press_button(1)
            assert receive_state() == "RECORDING"
            client.sendall(ws.send(CloseConnection(code=1000, reason="test complete")))
        server.should_exit = True
        thread.join(1.5)
        graceful = not thread.is_alive()
    finally:
        server.should_exit = True
        if thread.is_alive():
            server.force_exit = True
        thread.join(2)
        listener.close()
        context.close()
    assert graceful, "客户端已断开，但空闲事件流未退出，Uvicorn 关停被挂住"
    assert not thread.is_alive()

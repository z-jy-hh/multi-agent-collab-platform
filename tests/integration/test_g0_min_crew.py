"""G0 spike: prove the CrewAI leaf adapter works fully offline.

A local fake OpenAI-compatible HTTP server stands in for the LLM API, so this
verifies the CrewAI -> openai-client path without any external network access.
HTTP is served/consumed with the Python stack only (R26 discipline).
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Telemetry must be off before crewai is imported (C15).
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai import Agent, Crew, LLM, Task  # noqa: E402


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible endpoint: GET /v1/models, POST /v1/chat/completions."""

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [{"id": "fake-model", "object": "model"}]})
        else:
            self._send({"error": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._send(
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "fake-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "G0 spike OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4},
            }
        )


def test_min_crew_offline():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        llm = LLM(
            model="openai/fake-model",
            base_url=f"http://127.0.0.1:{port}/v1",
            api_key="sk-dummy",
        )
        agent = Agent(
            role="spike-echo",
            goal="reply with the canned message",
            backstory="a minimal agent used by the G0 spike",
            llm=llm,
            verbose=False,
        )
        task = Task(
            description="Reply with the canned message.",
            expected_output="G0 spike OK",
            agent=agent,
        )
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        result = crew.kickoff()
        assert "G0 spike OK" in str(result)
    finally:
        server.shutdown()
        server.server_close()

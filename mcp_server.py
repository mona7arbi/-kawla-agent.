# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3", "pypdf>=5.0"]
# ///
"""KAWLA's knowledge base as an MCP server (capstone requirement 1: one tool over MCP).

    uv run mcp_server.py                     # stdio (the agent launches it as a subprocess)
    uv run mcp_server.py --index raw         # configuration A: the PDF text as extracted (no cleaning)
    uv run mcp_server.py --http 8765         # Streamable HTTP on http://127.0.0.1:8765/mcp

Exposes:
- tool `search_knowledge`: BM25 search over the public PDF about the Complex's libraries + the service policy;
- resource `kawla://service-policy`: the (synthetic) reading-room service policy;
- resource `kawla://sources`: what the knowledge base contains.

Read-only by design: this server holds public documents only, no personal data and no write tools.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import knowledge  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402


def build(mode: str = "clean") -> MCPServer:
    idx = knowledge.index(mode)
    server = MCPServer("kawla-knowledge", title="KAWLA knowledge base",
                       instructions="Public documents of the King Abdulaziz Complex for Endowment Libraries "
                                    "(as used by the KAWLA assistant). Cite the chunk IDs you use.",
                       version="1.0.0", log_level="WARNING")

    @server.tool(name="search_knowledge")
    def search_knowledge(query: str, k: int = 5) -> list[dict]:
        """Search the Complex's documents: its libraries (history, founders, contents, counts of manuscripts and
        printed books) and the reading-room service policy (hours, booking rules, privacy).

        Returns passages with an ID to cite (DOC-p17-1 = document page 17, SP-3 = service policy section 3).
        If the answer is not in the passages, search again with other words before saying it is not available.

        Args:
            query: Arabic keywords, e.g. "عدد مخطوطات مكتبة المدرسة القازانية" or "ساعات قاعة الاطلاع".
            k: how many passages to return (1-8).
        """
        hits = idx.search(query, k=max(1, min(int(k), 8)))
        if not hits:
            return [{"note": "No passage matched. Try other or fewer words (e.g. the library's name)."}]
        return [{"id": h["id"], "source": h["source"], "about": h["about"], "text": h["text"]} for h in hits]

    @server.resource("kawla://service-policy", mime_type="text/markdown")
    def service_policy() -> str:
        """The reading-room service policy (synthetic, written for the capstone)."""
        return knowledge.POLICY.read_text(encoding="utf-8")

    @server.resource("kawla://sources", mime_type="text/markdown")
    def sources() -> str:
        """What the knowledge base contains and how it was indexed."""
        return (f"- {knowledge.DOC_TITLE} (PDF, public), index mode: {mode}, {len(idx.chunks)} chunks\n"
                "- سياسة خدمة الاطلاع (تجريبية)")

    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", choices=["raw", "clean"], default="clean", help="A = raw, B = clean (default)")
    ap.add_argument("--http", type=int, metavar="PORT")
    a = ap.parse_args()
    srv = build(a.index)
    if a.http:
        srv.run(transport="streamable-http", host="127.0.0.1", port=a.http)
    else:
        srv.run()

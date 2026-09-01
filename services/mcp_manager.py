import asyncio
import json
import logging
from contextlib import AsyncExitStack
import httpx
from google.genai import types
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from typing import Optional
import httpx
from google.genai import types
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)


def _is_connection_error(exc: Exception) -> bool:
    """Check if an exception is due to a closed or broken transport connection."""
    exc_type_name = type(exc).__name__
    exc_str = str(exc).lower()

    if exc_type_name in (
        "ClosedResourceError", "RemoteProtocolError", "EndOfStream",
        "TransportError", "ConnectError", "ConnectTimeout",
        "ReadTimeout", "WriteTimeout", "ConnectionResetError",
        "BrokenPipeError", "NetworkError"
    ):
        return True

    connection_keywords = [
        "closedresourceerror", "remoteprotocolerror", "peer closed connection",
        "incomplete chunked read", "connection reset", "broken pipe",
        "closed connection", "stream closed", "end of stream", "transport error"
    ]
    return any(kw in exc_str for kw in connection_keywords)


class MCPServerAdapter:
    def __init__(self, name: str, session: ClientSession):
        self.name = name
        self.session = session
        self.tool_mappings = {}
        self.list_resources_tool_name = None
        self.read_resource_tool_name = None

    def register_tool(self, original_name: str, mapped_name: str):
        self.tool_mappings[mapped_name] = original_name

    def register_resource_tools(self, list_name: str, read_name: str):
        self.list_resources_tool_name = list_name
        self.read_resource_tool_name = read_name

    async def execute_call(self, call) -> types.Part:
        gemini_name = call.name
        args = call.args if isinstance(call.args, dict) else {}

        if gemini_name == self.list_resources_tool_name:
            res = await asyncio.wait_for(self.session.list_resources(), timeout=30.0)
            all_resources = []
            for r in res.resources:
                all_resources.append({
                    "uri": str(r.uri),
                    "name": getattr(r, "name", ""),
                    "description": getattr(r, "description", ""),
                    "mimeType": getattr(r, "mimeType", "")
                })
            final_val = all_resources
            response_key = "result"

        elif gemini_name == self.read_resource_tool_name:
            uri = args.get("uri")
            if not uri:
                raise ValueError("Missing 'uri' argument")
            res = await asyncio.wait_for(self.session.read_resource(uri), timeout=120.0)
            texts = []
            for content in res.contents:
                if hasattr(content, "text") and content.text:
                    texts.append(content.text)
                elif hasattr(content, "blob") and content.blob:
                    texts.append(f"[Binary Blob: {getattr(content, 'mimeType', 'unknown')}]")
            final_val = "\n".join(texts)
            response_key = "result"

        else:
            mcp_name = self.tool_mappings.get(gemini_name, gemini_name)
            logger.info(f"Executing remote MCP tool: {mcp_name} on {self.name} with args {args}")
            result = await asyncio.wait_for(self.session.call_tool(mcp_name, args), timeout=120.0)
            final_val = self._extract_result_content(result)
            response_key = "error" if getattr(result, "isError", False) else "result"

        part = types.Part.from_function_response(
            name=gemini_name,
            response={response_key: final_val}
        )
        if hasattr(call, 'id') and call.id:
            part.function_response.id = call.id
        return part

    async def process_function_calls_as_parts(self, calls: list) -> list:
        parts = []
        for call in calls:
            gemini_name = call.name
            try:
                part = await self.execute_call(call)
            except Exception as e:
                err_msg = str(e) if str(e) else repr(e)
                logger.error(f"Error calling tool {gemini_name} on {self.name}: {err_msg}")
                part = types.Part.from_function_response(
                    name=gemini_name,
                    response={"error": err_msg}
                )
                if hasattr(call, 'id') and call.id:
                    part.function_response.id = call.id
            parts.append(part)
        return parts

    def _extract_result_content(self, result):
        if getattr(result, "structuredContent", None):
            return result.structuredContent
        if not result.content:
            return "Empty response"
        texts = [c.text for c in result.content if getattr(c, "type", "") == "text" and getattr(c, "text", "")]
        combined_text = "\n".join(texts)
        try:
            return json.loads(combined_text)
        except json.JSONDecodeError:
            return combined_text

class MCPConnectionManager:
    def __init__(self, config_json: str):
        self.config_json = config_json
        self.server_stacks = []
        self.adapters_map = {}
        self.mcp_declarations = []
        self._connected = False
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        async with self._connect_lock:
            if self._connected or not self.config_json or self.config_json == "{}" or self.config_json == "":
                return
            await self._do_connect()

    async def reconnect(self):
        """Force close and reconnect to all MCP servers (e.g. after a dropped SSE connection)."""
        async with self._connect_lock:
            logger.info("Reconnecting to MCP servers...")
            await self._do_close()
            if self.config_json and self.config_json != "{}":
                await self._do_connect()

    async def _do_connect(self):
        try:
            connections = json.loads(self.config_json)
        except Exception as e:
            logger.error(f"Failed to parse MCP config: {e}")
            return

        server_data = []
        tool_name_counts = {}

        for name, config in connections.items():
            transport_ctx = self._create_transport_context(name, config)
            if not transport_ctx:
                continue

            server_stack = AsyncExitStack()
            try:
                streams = await server_stack.enter_async_context(transport_ctx)
                read_stream, write_stream = streams[:2] if len(streams) >= 2 else streams

                session = await server_stack.enter_async_context(ClientSession(read_stream, write_stream))
                await asyncio.wait_for(session.initialize(), timeout=20.0)

                import re
                safe_server_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name).lower()
                adapter = MCPServerAdapter(name, session)

                tools_response = await asyncio.wait_for(session.list_tools(), timeout=20.0)

                capabilities = session.get_server_capabilities()
                has_resources = bool(capabilities and getattr(capabilities, "resources", None))

                # Count tool names to detect collisions across all servers
                for t in tools_response.tools:
                    tool_name_counts[t.name] = tool_name_counts.get(t.name, 0) + 1

                if has_resources:
                    tool_name_counts["list_resources"] = tool_name_counts.get("list_resources", 0) + 1
                    tool_name_counts["read_resource"] = tool_name_counts.get("read_resource", 0) + 1

                server_data.append({
                    "name": name,
                    "safe_name": safe_server_name,
                    "adapter": adapter,
                    "tools": tools_response.tools,
                    "has_resources": has_resources,
                    "stack": server_stack
                })
                self.server_stacks.append(server_stack)
            except Exception as e:
                logger.error(f"Failed to connect to MCP server {name}: {e}")
                await server_stack.aclose()

        # Registration phase (only prefix if there's a collision)
        for data in server_data:
            server_name = data["name"]
            safe_name = data["safe_name"]
            adapter = data["adapter"]

            for tool in data["tools"]:
                mapped_name = f"{safe_name}_{tool.name}" if tool_name_counts[tool.name] > 1 else tool.name

                adapter.register_tool(tool.name, mapped_name)
                self.adapters_map[mapped_name] = adapter

                input_schema = tool.inputSchema if hasattr(tool, "inputSchema") else tool.input_schema
                if "type" not in input_schema:
                    input_schema["type"] = "object"

                self.mcp_declarations.append(types.FunctionDeclaration(
                    name=mapped_name,
                    description=tool.description or "",
                    parameters_json_schema=input_schema
                ))

            if data["has_resources"]:
                list_name = f"{safe_name}_list_resources" if tool_name_counts.get("list_resources", 0) > 1 else "list_resources"
                read_name = f"{safe_name}_read_resource" if tool_name_counts.get("read_resource", 0) > 1 else "read_resource"

                adapter.register_resource_tools(list_name, read_name)

                self.adapters_map[list_name] = adapter
                self.mcp_declarations.append(types.FunctionDeclaration(
                    name=list_name,
                    description=f"List available resources from the {server_name} server.",
                    parameters_json_schema={"type": "object", "properties": {}}
                ))

                self.adapters_map[read_name] = adapter
                self.mcp_declarations.append(types.FunctionDeclaration(
                    name=read_name,
                    description=f"Read a specific resource from the {server_name} server using its URI.",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {"uri": {"type": "string", "description": "The URI of the resource to read"}},
                        "required": ["uri"]
                    }
                ))

            logger.info(f"Connected to MCP server: {server_name} (loaded {len(data['tools'])} tools, resources: {data['has_resources']})")

        if server_data:
            self._connected = True

    async def process_function_calls(self, calls: list) -> list:
        response_parts = []
        for fc in calls:
            adapter = self.adapters_map.get(fc.name)
            if adapter:
                try:
                    part = await adapter.execute_call(fc)
                except Exception as e:
                    if _is_connection_error(e):
                        logger.warning(f"Connection error executing {fc.name} on {adapter.name}: {e}. Reconnecting...")
                        await self.reconnect()
                        # Retry once with newly reconnected adapter
                        new_adapter = self.adapters_map.get(fc.name)
                        if new_adapter:
                            try:
                                part = await new_adapter.execute_call(fc)
                            except Exception as retry_err:
                                err_msg = str(retry_err) if str(retry_err) else repr(retry_err)
                                logger.error(f"Error calling tool {fc.name} on {new_adapter.name} after retry: {err_msg}")
                                part = types.Part.from_function_response(name=fc.name, response={"error": err_msg})
                                if hasattr(fc, 'id') and fc.id:
                                    part.function_response.id = fc.id
                        else:
                            part = types.Part.from_function_response(name=fc.name, response={"error": f"Tool {fc.name} unavailable after reconnect"})
                            if hasattr(fc, 'id') and fc.id:
                                part.function_response.id = fc.id
                    else:
                        err_msg = str(e) if str(e) else repr(e)
                        logger.error(f"Error calling tool {fc.name} on {adapter.name}: {err_msg}")
                        part = types.Part.from_function_response(name=fc.name, response={"error": err_msg})
                        if hasattr(fc, 'id') and fc.id:
                            part.function_response.id = fc.id
                response_parts.append(part)
            else:
                part = types.Part.from_function_response(name=fc.name, response={"error": "Tool not found"})
                if hasattr(fc, 'id') and fc.id:
                    part.function_response.id = fc.id
                response_parts.append(part)
        return response_parts

    async def _do_close(self):
        for s in self.server_stacks:
            try:
                await s.aclose()
            except Exception:
                pass
        self.server_stacks.clear()
        self.adapters_map.clear()
        self.mcp_declarations.clear()
        self._connected = False

    async def close(self):
        async with self._connect_lock:
            await self._do_close()

    def _create_transport_context(self, name: str, config: dict):
        url = config.get("url")
        mcp_type = config.get("type")
        headers = config.get("headers", {})
        if url and mcp_type == "sse":
            return sse_client(url=url, headers=headers, sse_read_timeout=300.0)
        if url:
            return streamable_http_client(
                url=url,
                http_client=httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(10.0, read=300.0, write=60.0, connect=10.0))
            )
        return None
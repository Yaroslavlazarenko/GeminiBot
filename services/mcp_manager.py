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

    async def process_function_calls_as_parts(self, calls: list) -> list:
        parts = []
        for call in calls:
            gemini_name = call.name
            try:
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
                    uri = call.args.get("uri")
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
                    logger.info(f"Executing remote MCP tool: {mcp_name} on {self.name} with args {call.args}")
                    result = await asyncio.wait_for(self.session.call_tool(mcp_name, call.args), timeout=120.0)
                    final_val = self._extract_result_content(result)
                    response_key = "error" if getattr(result, "isError", False) else "result"
                
                part = types.Part.from_function_response(
                    name=gemini_name,
                    response={response_key: final_val}
                )
            except Exception as e:
                logger.error(f"Error calling tool {gemini_name} on {self.name}: {e}")
                part = types.Part.from_function_response(
                    name=gemini_name,
                    response={"error": str(e)}
                )
            # Make sure to attach the id so the model knows which call this response corresponds to
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

    async def connect(self):
        if self._connected or not self.config_json or self.config_json == "{}" or self.config_json == "":
            return
        
        try:
            connections = json.loads(self.config_json)
        except Exception as e:
            logger.error(f"Failed to parse MCP config: {e}")
            return

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
                
                for t in tools_response.tools:
                    # Prefix to avoid collisions if multiple servers have the same tool name
                    mapped_name = f"{safe_server_name}_{t.name}"
                    adapter.register_tool(t.name, mapped_name)
                    self.adapters_map[mapped_name] = adapter
                    
                    input_schema = t.inputSchema if hasattr(t, "inputSchema") else t.input_schema
                    if "type" not in input_schema:
                        input_schema["type"] = "object"
                        
                    self.mcp_declarations.append(types.FunctionDeclaration(
                        name=mapped_name,
                        description=t.description or "",
                        parameters_json_schema=input_schema
                    ))
                    
                capabilities = session.get_server_capabilities()
                has_resources = bool(capabilities and getattr(capabilities, "resources", None))
                
                if has_resources:
                    list_name = f"{safe_server_name}_list_resources"
                    read_name = f"{safe_server_name}_read_resource"
                    
                    adapter.register_resource_tools(list_name, read_name)
                    
                    self.adapters_map[list_name] = adapter
                    self.mcp_declarations.append(types.FunctionDeclaration(
                        name=list_name,
                        description=f"List available resources from the {name} server.",
                        parameters_json_schema={"type": "object", "properties": {}}
                    ))
                    
                    self.adapters_map[read_name] = adapter
                    self.mcp_declarations.append(types.FunctionDeclaration(
                        name=read_name,
                        description=f"Read a specific resource from the {name} server using its URI.",
                        parameters_json_schema={
                            "type": "object",
                            "properties": {"uri": {"type": "string", "description": "The URI of the resource to read"}},
                            "required": ["uri"]
                        }
                    ))
                    
                self.server_stacks.append(server_stack)
                logger.info(f"Connected to MCP server: {name} (loaded {len(tools_response.tools)} tools, resources: {has_resources})")
            except Exception as e:
                logger.error(f"Failed to connect to MCP server {name}: {e}")
                await server_stack.aclose()
                
        self._connected = True

    async def process_function_calls(self, calls: list) -> list:
        response_parts = []
        for fc in calls:
            adapter = self.adapters_map.get(fc.name)
            if adapter:
                parts = await adapter.process_function_calls_as_parts([fc])
                response_parts.extend(parts)
            else:
                part = types.Part.from_function_response(name=fc.name, response={"error": "Tool not found"})
                part.function_response.id = fc.id
                response_parts.append(part)
        return response_parts

    async def close(self):
        for s in self.server_stacks:
            try:
                await s.aclose()
            except Exception:
                pass

    def _create_transport_context(self, name: str, config: dict):
        url = config.get("url")
        mcp_type = config.get("type")
        headers = config.get("headers", {})
        if url and mcp_type == "sse":
            return sse_client(url=url, headers=headers)
        if url:
            return streamable_http_client(
                url=url, 
                http_client=httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(5.0, read=300.0))
            )
        return None
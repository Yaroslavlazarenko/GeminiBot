import json
import logging
import secrets
from aiohttp import web
from core.database import DatabaseManager
from core.config import Config

logger = logging.getLogger(__name__)

# Store valid tokens mapped to their expiry or just track active tokens
# Format: { "token": True }
VALID_TOKENS = {}

def create_admin_session() -> str:
    """Generate a one-time token for the admin panel."""
    token = secrets.token_urlsafe(16)
    VALID_TOKENS[token] = True
    return token

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MiaBot - Admin Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen text-gray-800">
    <div class="max-w-4xl mx-auto py-10 px-4">
        <h1 class="text-3xl font-bold mb-8 text-center text-blue-600">MiaBot Admin Panel</h1>
        
        <div id="alert" class="hidden mb-4 p-4 rounded-md text-white text-center"></div>

        <form id="settings-form" class="bg-white shadow-md rounded px-8 pt-6 pb-8 mb-4">
            
            <h2 class="text-xl font-semibold mb-4 border-b pb-2">Persona & System Prompt</h2>
            <div class="mb-6">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="system_instruction">
                    System Instruction (Prompt)
                </label>
                <textarea id="system_instruction" class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" rows="12"></textarea>
            </div>

            <h2 class="text-xl font-semibold mb-4 border-b pb-2">AI Models, Endpoint & API Keys</h2>
            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="gemini_api_model">
                    Main Persona Model (Mia)
                </label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="gemini_api_model" type="text" placeholder="e.g. gemini-3.5-flash">
            </div>

            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="gemini_gatekeeper_model">
                    Gatekeeper Model (Fast filter)
                </label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="gemini_gatekeeper_model" type="text" placeholder="e.g. gemini-3.1-flash-lite">
            </div>

            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="gemini_base_url">
                    Gemini Base URL (Override)
                </label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="gemini_base_url" type="text" placeholder="Leave empty for default Google API">
            </div>

            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="gemini_api_key">
                    Base Gemini API Key (Primary)
                </label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="gemini_api_key" type="password" placeholder="AIzaSy...">
            </div>

            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="gemini_api_keys">
                    Additional Gemini API Keys (Comma-separated, for rotation)
                </label>
                <textarea class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="gemini_api_keys" rows="2" placeholder="key2, key3, key4"></textarea>
                <p class="text-xs text-gray-500 mt-1">If any key encounters a 429 (Quota), 500, or 503 error, the bot will automatically rotate to the next key and transparently retry the request.</p>
            </div>

            <h2 class="text-xl font-semibold mb-4 border-b pb-2 mt-8">MCP Servers (Model Context Protocol)</h2>
            <div class="mb-6">
                <p class="text-sm text-gray-600 mb-4">Add, remove, and manage external MCP server tools visually instead of writing raw JSON configurations.</p>
                
                <div class="overflow-x-auto border border-gray-200 rounded-md">
                    <table class="min-w-full divide-y divide-gray-200" id="mcp-table">
                        <thead class="bg-gray-50">
                            <tr>
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/3">Server Name / Alias</th>
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/2">SSE Endpoints / URLs</th>
                                <th scope="col" class="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
                            </tr>
                        </thead>
                        <tbody class="bg-white divide-y divide-gray-200" id="mcp-rows">
                            <!-- Populated dynamically via JS -->
                        </tbody>
                    </table>
                </div>
                
                <button type="button" onclick="addMcpRow()" class="mt-3 inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-green-600 hover:bg-green-700 focus:outline-none focus:shadow-outline">
                    ➕ Add MCP Server Row
                </button>
            </div>

            <div class="flex items-center justify-between mt-8">
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-3 px-6 rounded focus:outline-none focus:shadow-outline w-full text-lg transition duration-200" type="submit">
                    Save All Configurations
                </button>
            </div>
        </form>
    </div>

    <script>
        function addMcpRow(name = '', url = '') {
            const tbody = document.getElementById('mcp-rows');
            const rowId = 'mcp-row-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
            
            const tr = document.createElement('tr');
            tr.id = rowId;
            tr.className = 'hover:bg-gray-50 transition duration-150';
            
            tr.innerHTML = `
                <td class="px-4 py-3">
                    <input type="text" value="${name}" placeholder="e.g. math" class="mcp-name shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-gray-300 rounded-md py-1.5 px-3 border">
                </td>
                <td class="px-4 py-3">
                    <input type="url" value="${url}" placeholder="https://mathematics.fastmcp.app/mcp" class="mcp-url shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-gray-300 rounded-md py-1.5 px-3 border">
                </td>
                <td class="px-4 py-3 text-center">
                    <button type="button" onclick="removeMcpRow('${rowId}')" class="text-red-600 hover:text-red-900 font-bold px-3 py-1.5 border border-red-200 hover:border-red-400 rounded-md transition duration-150 bg-red-50 hover:bg-red-100">
                        Delete
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        }

        function removeMcpRow(rowId) {
            const row = document.getElementById(rowId);
            if (row) {
                row.remove();
            }
        }

        function serializeMcpTable() {
            const names = document.querySelectorAll('.mcp-name');
            const urls = document.querySelectorAll('.mcp-url');
            const config = {};
            
            for (let i = 0; i < names.length; i++) {
                const name = names[i].value.trim();
                const url = urls[i].value.trim();
                if (name && url) {
                    config[name] = { "url": url };
                }
            }
            return JSON.stringify(config);
        }

        function populateMcpTable(mcpJsonStr) {
            const tbody = document.getElementById('mcp-rows');
            tbody.innerHTML = '';
            
            try {
                const config = JSON.parse(mcpJsonStr || '{}');
                let hasRows = false;
                for (const [name, value] of Object.entries(config)) {
                    const url = value && value.url ? value.url : '';
                    addMcpRow(name, url);
                    hasRows = true;
                }
                if (!hasRows) {
                    addMcpRow(); // add one empty helper row
                }
            } catch (e) {
                console.error("Error parsing MCP JSON configuration:", e);
                addMcpRow();
            }
        }

        function getToken() {
            const params = new URLSearchParams(window.location.search);
            return params.get('token') || '';
        }

        async function loadSettings() {
            try {
                const token = getToken();
                const url = token ? `/api/settings?token=${token}` : '/api/settings';
                const res = await fetch(url);
                const data = await res.json();
                document.getElementById('gemini_api_model').value = data.gemini_api_model || '';
                document.getElementById('gemini_gatekeeper_model').value = data.gemini_gatekeeper_model || '';
                document.getElementById('gemini_base_url').value = data.gemini_base_url || '';
                document.getElementById('gemini_api_key').value = data.gemini_api_key || '';
                document.getElementById('gemini_api_keys').value = data.gemini_api_keys || '';
                document.getElementById('system_instruction').value = data.system_instruction || '';
                
                populateMcpTable(data.mcp_servers_config);
            } catch (err) {
                showAlert("Failed to load settings", "red");
            }
        }

        function showAlert(msg, color) {
            const alert = document.getElementById('alert');
            alert.textContent = msg;
            alert.className = `mb-4 p-4 rounded-md text-white text-center bg-${color}-500 block font-semibold shadow`;
            setTimeout(() => alert.className = "hidden", 4000);
        }

        document.getElementById('settings-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            // Build MCP JSON config from visual table rows
            const mcpConfig = serializeMcpTable();

            const payload = {
                gemini_api_model: document.getElementById('gemini_api_model').value.trim(),
                gemini_gatekeeper_model: document.getElementById('gemini_gatekeeper_model').value.trim(),
                gemini_base_url: document.getElementById('gemini_base_url').value.trim(),
                gemini_api_key: document.getElementById('gemini_api_key').value.trim(),
                gemini_api_keys: document.getElementById('gemini_api_keys').value.trim(),
                system_instruction: document.getElementById('system_instruction').value,
                mcp_servers_config: mcpConfig
            };

            try {
                const token = getToken();
                const url = token ? `/api/settings?token=${token}` : '/api/settings';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                
                if (res.ok) {
                    showAlert("Settings saved successfully! The bot will apply them dynamically on the next request.", "green");
                } else {
                    showAlert("Failed to save settings.", "red");
                }
            } catch (err) {
                showAlert("Error connecting to the server.", "red");
            }
        });

        loadSettings();
    </script>
</body>
</html>
"""

def setup_admin_app(db: DatabaseManager, config: Config) -> web.Application:
    app = web.Application()
    
    @web.middleware
    async def token_auth_middleware(request, handler):
        # 1. Check if token is in URL query params
        query_token = request.query.get("token")
        
        # 2. Check if token is in cookies
        cookie_token = request.cookies.get("admin_session")

        valid_token = None
        if query_token and query_token in VALID_TOKENS:
            valid_token = query_token
        elif cookie_token and cookie_token in VALID_TOKENS:
            valid_token = cookie_token

        if not valid_token:
            return web.Response(
                status=401, 
                text="Unauthorized. Please use the /admin command in Telegram to generate a secure access link."
            )
            
        # Proceed with request
        response = await handler(request)
        
        # If authenticated via URL, set cookie so they can refresh
        if query_token and not cookie_token:
            response.set_cookie("admin_session", valid_token, max_age=86400, httponly=True)
            
        return response

    app.middlewares.append(token_auth_middleware)

    async def handle_index(request):
        return web.Response(text=HTML_TEMPLATE, content_type='text/html')

    async def handle_get_settings(request):
        settings = await db.get_system_settings()
        return web.json_response({
            "gemini_api_model": settings.get("gemini_api_model") or config.gemini_api_model,
            "gemini_gatekeeper_model": settings.get("gemini_gatekeeper_model") or config.gemini_gatekeeper_model,
            "gemini_base_url": settings.get("gemini_base_url") or (config.gemini_base_url if config.gemini_base_url else ""),
            "gemini_api_key": settings.get("gemini_api_key") or config.gemini_api_key,
            "gemini_api_keys": settings.get("gemini_api_keys") or config.gemini_api_keys,
            "system_instruction": settings.get("system_instruction") or "",
            "mcp_servers_config": settings.get("mcp_servers_config") or config.mcp_servers_config
        })

    async def handle_post_settings(request):
        try:
            data = await request.json()
            updates = {
                "gemini_api_model": data.get("gemini_api_model", ""),
                "gemini_gatekeeper_model": data.get("gemini_gatekeeper_model", ""),
                "gemini_base_url": data.get("gemini_base_url", ""),
                "gemini_api_key": data.get("gemini_api_key", ""),
                "gemini_api_keys": data.get("gemini_api_keys", ""),
                "system_instruction": data.get("system_instruction", ""),
                "mcp_servers_config": data.get("mcp_servers_config", "{}")
            }
            await db.update_system_settings(updates)
            logger.info("System settings updated via Admin Panel")
            return web.json_response({"status": "success"})
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return web.json_response({"error": str(e)}, status=400)

    app.router.add_get('/', handle_index)
    app.router.add_get('/api/settings', handle_get_settings)
    app.router.add_post('/api/settings', handle_post_settings)

    return app

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
    <style>
        .json-editor { font-family: monospace; white-space: pre; }
    </style>
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

            <h2 class="text-xl font-semibold mb-4 border-b pb-2">AI Models & Endpoint</h2>
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

            <div class="mb-6">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="gemini_base_url">
                    Gemini Base URL (Override)
                </label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="gemini_base_url" type="text" placeholder="Leave empty for default Google API">
            </div>

            <h2 class="text-xl font-semibold mb-4 border-b pb-2 mt-8">MCP Servers Configuration (JSON)</h2>
            <div class="mb-6">
                <p class="text-sm text-gray-600 mb-2">Example: <code>{"math": {"url": "https://mathematics.fastmcp.app/mcp"}}</code></p>
                <textarea id="mcp_servers_config" class="json-editor shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" rows="10"></textarea>
            </div>

            <div class="flex items-center justify-between">
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline w-full" type="submit">
                    Save Configuration
                </button>
            </div>
        </form>
    </div>

    <script>
        async function loadSettings() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                document.getElementById('gemini_api_model').value = data.gemini_api_model || '';
                document.getElementById('gemini_gatekeeper_model').value = data.gemini_gatekeeper_model || '';
                document.getElementById('gemini_base_url').value = data.gemini_base_url || '';
                document.getElementById('system_instruction').value = data.system_instruction || '';
                
                let mcpJson = data.mcp_servers_config || '{}';
                try { mcpJson = JSON.stringify(JSON.parse(mcpJson), null, 4); } catch(e) {}
                document.getElementById('mcp_servers_config').value = mcpJson;
            } catch (err) {
                showAlert("Failed to load settings", "red");
            }
        }

        function showAlert(msg, color) {
            const alert = document.getElementById('alert');
            alert.textContent = msg;
            alert.className = `mb-4 p-4 rounded-md text-white text-center bg-${color}-500 block`;
            setTimeout(() => alert.className = "hidden", 3000);
        }

        document.getElementById('settings-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            // Validate JSON
            let mcpConfig = document.getElementById('mcp_servers_config').value;
            try {
                if (mcpConfig.trim() !== '') {
                    JSON.parse(mcpConfig);
                } else {
                    mcpConfig = "{}";
                }
            } catch (e) {
                showAlert("Invalid JSON in MCP configuration!", "red");
                return;
            }

            const payload = {
                gemini_api_model: document.getElementById('gemini_api_model').value.trim(),
                gemini_gatekeeper_model: document.getElementById('gemini_gatekeeper_model').value.trim(),
                gemini_base_url: document.getElementById('gemini_base_url').value.trim(),
                system_instruction: document.getElementById('system_instruction').value,
                mcp_servers_config: mcpConfig
            };

            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                
                if (res.ok) {
                    showAlert("Settings saved successfully! The bot will use them on the next request.", "green");
                } else {
                    showAlert("Failed to save settings.", "red");
                }
            } catch (err) {
                showAlert("Error connecting to server.", "red");
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
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

            <div class="mb-6">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="sticker_set_names">
                    Telegram Sticker Sets (Comma-separated)
                </label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="sticker_set_names" type="text" placeholder="e.g. Animals, UtyaDuck">
                <p class="text-xs text-gray-500 mt-1">The bot will use stickers from these sets to express emotions. If multiple packs are provided, it will randomly pick a matching sticker across all of them.</p>
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
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style="width:15%">Server Name</th>
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style="width:30%">URL</th>
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style="width:10%">Type</th>
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style="width:35%">Headers (JSON)</th>
                                <th scope="col" class="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider" style="width:10%">Action</th>
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

            <h2 class="text-xl font-semibold mb-4 border-b pb-2 mt-8">Proactive Behavior</h2>
            <p class="text-sm text-gray-600 mb-4">Configure Mia's autonomous research and messaging behavior. She will periodically search the web, accumulate world knowledge, and optionally reach out to users/groups.</p>

            <div class="grid grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="flex items-center space-x-2 cursor-pointer">
                        <input type="checkbox" id="proactive_research_enabled" class="rounded border-gray-300 text-blue-600 focus:ring-blue-500 h-5 w-5">
                        <span class="text-sm font-bold text-gray-700">Enable Web Research</span>
                    </label>
                    <p class="text-xs text-gray-500 mt-1">Mia will periodically search the web for interesting topics and save findings to her world memory.</p>
                </div>
                <div>
                    <label class="flex items-center space-x-2 cursor-pointer">
                        <input type="checkbox" id="proactive_messaging_enabled" class="rounded border-gray-300 text-blue-600 focus:ring-blue-500 h-5 w-5">
                        <span class="text-sm font-bold text-gray-700">Enable Proactive Messaging</span>
                    </label>
                    <p class="text-xs text-gray-500 mt-1">Mia can decide to write to users or groups on her own.</p>
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_research_interval">
                        Research Interval (hours)
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_research_interval" type="number" step="0.5" min="0.5" value="2">
                </div>
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_messaging_interval">
                        Messaging Check Interval (hours)
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_messaging_interval" type="number" step="0.5" min="0.5" value="1.5">
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_awake_start">
                        Awake From (hour, Odessa time)
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_awake_start" type="number" step="1" min="0" max="23" value="9">
                    <p class="text-xs text-gray-500 mt-1">Mia won't do anything proactive before this hour.</p>
                </div>
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_awake_end">
                        Sleep At (hour, Odessa time)
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_awake_end" type="number" step="1" min="0" max="23" value="0">
                    <p class="text-xs text-gray-500 mt-1">0 = midnight. Proactive actions stop at this hour.</p>
                </div>
            </div>

            <div class="grid grid-cols-3 gap-4 mb-4">
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_min_silence">
                        Min Silence (hours)
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_min_silence" type="number" step="1" min="1" value="12">
                    <p class="text-xs text-gray-500 mt-1">Don't message if user was active less than N hours ago.</p>
                </div>
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_max_ignored">
                        Max Consecutive Ignored
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_max_ignored" type="number" step="1" min="1" value="2">
                    <p class="text-xs text-gray-500 mt-1">Stop messaging after N unanswered messages in a row.</p>
                </div>
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_probability">
                        Messaging Probability
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_probability" type="number" step="0.05" min="0" max="1" value="0.3">
                    <p class="text-xs text-gray-500 mt-1">Random pre-filter (0.0 - 1.0). Higher = more frequent.</p>
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_max_memory_entries">
                        World Memory Max Entries
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_max_memory_entries" type="number" step="5" min="10" value="50">
                </div>
                <div>
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_max_memory_chars">
                        World Memory Max Chars (in prompt)
                    </label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_max_memory_chars" type="number" step="1000" min="1000" value="8000">
                </div>
            </div>

            <div class="mb-6">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="proactive_research_seed">
                    Research Seed Topics (optional)
                </label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="proactive_research_seed" type="text" placeholder="e.g. technology, music, art, science, Ukraine, pop culture">
                <p class="text-xs text-gray-500 mt-1">General interest areas to guide Mia's research direction. Leave empty for fully autonomous topic selection.</p>
            </div>

            <div class="flex items-center justify-between mt-8">
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-3 px-6 rounded focus:outline-none focus:shadow-outline w-full text-lg transition duration-200" type="submit">
                    Save All Configurations
                </button>
            </div>
        </form>
    </div>

    <script>
        function addMcpRow(name = '', url = '', type = '', headers = '') {
            const tbody = document.getElementById('mcp-rows');
            const rowId = 'mcp-row-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);

            const tr = document.createElement('tr');
            tr.id = rowId;
            tr.className = 'hover:bg-gray-50 transition duration-150';

            // Format headers for display
            let headersDisplay = '';
            if (typeof headers === 'object' && headers !== null) {
                headersDisplay = Object.keys(headers).length > 0 ? JSON.stringify(headers, null, 2) : '';
            } else if (typeof headers === 'string') {
                headersDisplay = headers;
            }

            const sseSelected = type === 'sse' ? 'selected' : '';
            const streamSelected = type !== 'sse' ? 'selected' : '';

            tr.innerHTML = `
                <td class="px-4 py-3">
                    <input type="text" value="${name}" placeholder="e.g. exa" class="mcp-name shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-gray-300 rounded-md py-1.5 px-3 border">
                </td>
                <td class="px-4 py-3">
                    <input type="url" value="${url}" placeholder="https://mcp.example.com/sse" class="mcp-url shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-gray-300 rounded-md py-1.5 px-3 border">
                </td>
                <td class="px-4 py-3">
                    <select class="mcp-type shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-gray-300 rounded-md py-1.5 px-2 border">
                        <option value="sse" ${sseSelected}>SSE</option>
                        <option value="streamable" ${streamSelected}>Streamable</option>
                    </select>
                </td>
                <td class="px-4 py-3">
                    <textarea placeholder='{"x-api-key": "..."}' rows="2" class="mcp-headers shadow-sm focus:ring-blue-500 focus:border-blue-500 block w-full sm:text-sm border-gray-300 rounded-md py-1.5 px-3 border font-mono text-xs">${headersDisplay}</textarea>
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
            const types = document.querySelectorAll('.mcp-type');
            const headers = document.querySelectorAll('.mcp-headers');
            const config = {};

            for (let i = 0; i < names.length; i++) {
                const name = names[i].value.trim();
                const url = urls[i].value.trim();
                const type = types[i].value;
                const headersStr = headers[i].value.trim();
                if (name && url) {
                    const entry = { "url": url, "type": type };
                    if (headersStr) {
                        try {
                            entry["headers"] = JSON.parse(headersStr);
                        } catch (e) {
                            showAlert(`Invalid JSON in headers for "${name}". Please fix it.`, "red");
                            return null;
                        }
                    }
                    config[name] = entry;
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
                    const type = value && value.type ? value.type : 'sse';
                    const headers = value && value.headers ? value.headers : {};
                    addMcpRow(name, url, type, headers);
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
                document.getElementById('sticker_set_names').value = data.sticker_set_names || 'Animals';
                
                populateMcpTable(data.mcp_servers_config);

                // Proactive settings
                const p = data.proactive || {};
                document.getElementById('proactive_research_enabled').checked = p.research_enabled !== false;
                document.getElementById('proactive_messaging_enabled').checked = p.messaging_enabled !== false;
                document.getElementById('proactive_research_interval').value = p.research_interval_hours || 2;
                document.getElementById('proactive_messaging_interval').value = p.messaging_check_interval_hours || 1.5;
                document.getElementById('proactive_awake_start').value = p.awake_hour_start ?? 9;
                document.getElementById('proactive_awake_end').value = p.awake_hour_end ?? 0;
                document.getElementById('proactive_min_silence').value = p.messaging_min_silence_hours || 12;
                document.getElementById('proactive_max_ignored').value = p.messaging_max_consecutive_ignored || 2;
                document.getElementById('proactive_probability').value = p.messaging_probability || 0.3;
                document.getElementById('proactive_max_memory_entries').value = p.world_memory_max_entries || 50;
                document.getElementById('proactive_max_memory_chars').value = p.world_memory_max_chars || 8000;
                document.getElementById('proactive_research_seed').value = p.research_topics_seed || '';
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
            if (mcpConfig === null) return; // validation failed

            const payload = {
                gemini_api_model: document.getElementById('gemini_api_model').value.trim(),
                gemini_gatekeeper_model: document.getElementById('gemini_gatekeeper_model').value.trim(),
                gemini_base_url: document.getElementById('gemini_base_url').value.trim(),
                gemini_api_key: document.getElementById('gemini_api_key').value.trim(),
                gemini_api_keys: document.getElementById('gemini_api_keys').value.trim(),
                system_instruction: document.getElementById('system_instruction').value,
                sticker_set_names: document.getElementById('sticker_set_names').value.trim(),
                mcp_servers_config: mcpConfig,
                proactive: {
                    research_enabled: document.getElementById('proactive_research_enabled').checked,
                    messaging_enabled: document.getElementById('proactive_messaging_enabled').checked,
                    research_interval_hours: parseFloat(document.getElementById('proactive_research_interval').value) || 2,
                    messaging_check_interval_hours: parseFloat(document.getElementById('proactive_messaging_interval').value) || 1.5,
                    awake_hour_start: parseInt(document.getElementById('proactive_awake_start').value) ?? 9,
                    awake_hour_end: parseInt(document.getElementById('proactive_awake_end').value) ?? 0,
                    messaging_min_silence_hours: parseFloat(document.getElementById('proactive_min_silence').value) || 12,
                    messaging_max_consecutive_ignored: parseInt(document.getElementById('proactive_max_ignored').value) || 2,
                    messaging_probability: parseFloat(document.getElementById('proactive_probability').value) || 0.3,
                    world_memory_max_entries: parseInt(document.getElementById('proactive_max_memory_entries').value) || 50,
                    world_memory_max_chars: parseInt(document.getElementById('proactive_max_memory_chars').value) || 8000,
                    research_topics_seed: document.getElementById('proactive_research_seed').value.trim()
                }
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

def setup_admin_app(db: DatabaseManager, config: Config, bot=None) -> web.Application:
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
            "sticker_set_names": settings.get("sticker_set_names") or settings.get("sticker_set_name") or "Animals",
            "mcp_servers_config": settings.get("mcp_servers_config") or config.mcp_servers_config,
            "proactive": settings.get("proactive", {})
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
                "sticker_set_names": data.get("sticker_set_names", "Animals"),
                "mcp_servers_config": data.get("mcp_servers_config", "{}"),
                "proactive": data.get("proactive", {})
            }
            await db.update_system_settings(updates)
            logger.info("System settings updated via Admin Panel")
            
            # Sync system_instructions.md on disk
            try:
                if updates.get("system_instruction"):
                    with open("system_instructions.md", "w", encoding="utf-8") as f:
                        f.write(updates["system_instruction"])
            except Exception as fe:
                logger.warning(f"Could not write system_instructions.md to disk: {fe}")
            
            # Trigger sticker sync in background
            if bot:
                from services.sticker_service import StickerService
                from core.key_manager import get_key_manager
                import asyncio
                packs_raw = updates.get("sticker_set_names", "Animals")
                pack_names = [p.strip() for p in packs_raw.split(',') if p.strip()]
                asyncio.create_task(StickerService.sync_sticker_packs(bot, db, get_key_manager(), pack_names))
                
            return web.json_response({"status": "success"})
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return web.json_response({"error": str(e)}, status=400)

    app.router.add_get('/', handle_index)
    app.router.add_get('/api/settings', handle_get_settings)
    app.router.add_post('/api/settings', handle_post_settings)

    return app

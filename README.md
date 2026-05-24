[🇬🇧 English](README.md) | [🇷🇺 Русский](README.ru.md) | [🇺🇦 Українська](README.uk.md)

# Mia - Advanced Telegram AI Assistant

Mia is a highly sophisticated, persona-driven Telegram bot built on top of the **Google Gemini API** (GenAI SDK) and **FastMCP**. It utilizes a modern, clean architecture backed by MongoDB and features a dual-model "Gatekeeper" routing system, dynamic tool orchestration, and a secure Web Admin panel.

## 🌟 Core Features

- **Dual-Model Architecture (Gatekeeper Pattern):** 
  - A fast, lightweight model (`gemini-3.1-flash-lite`) acts as a smart filter. In groups, it actively searches the DB history and strictly ignores chatter unless Mia is explicitly addressed or the context is highly relevant.
  - The main persona model (`gemini-3.5-flash`) is only invoked when necessary, saving tokens and preventing spam. The AI can also silently abort its response mid-generation if it realizes it shouldn't intrude.
- **Smart Memory & Context:**
  - **Permanent Chat History:** Stores full, un-summarized logs in MongoDB. Mia can use tools to search past conversations by keyword or date.
  - **Cross-Chat User Memory:** Mia observes user preferences, secrets, and traits, permanently saving them. Harmless facts are globally available across all chats, while sensitive facts are programmatically sandboxed to the specific chat where they were shared.
  - **Reply & Quote Awareness:** Intercepts Telegram's native `reply` and `forward` attributes, injecting exact speaker attributions so the AI perfectly understands the conversational flow.
- **Advanced Media Handling:**
  - **Burst Debouncing:** Queues rapid-fire messages and photo albums (3-second debounce), merging them into a single multimodal prompt to prevent the bot from spamming responses.
  - **AI Vision Sticker Catalog:** Background workers automatically download your configured sticker packs, run them through Gemini Vision to generate visual descriptions, and cache them in the DB. Mia then uses a `search_stickers` tool to pick the exact ID of the perfect sticker.
  - **Video Notes ("Кружочки"):** Automatically transcribes audio via Groq Whisper and runs a quick inline visual analysis via Gemini Vision to store the video's contents in the permanent text history.
- **Native FastMCP Integration:**
  - **Local Tools:** Mia natively uses tools to send Telegram reactions, reply to specific messages with quotes, send targeted stickers, and generate **real voice messages** (via ElevenLabs TTS). If TTS fails, the AI handles the error contextually in text.
  - **Remote MCP Servers:** Connect the bot to external HTTP/SSE MCP servers (like Exa Search). The proxy fetches tools dynamically and orchestrates the function-calling loop autonomously.
- **Secure Web Admin Panel:** Manage models, the Gemini Base URL (proxy support), MCP server configurations, multiple sticker packs, and edit Mia's System Prompt on the fly. Access is strictly secured via one-time tokens generated in Telegram.

---

## 🚀 Quick Start (Docker)

Deployment is strictly handled via Docker and Docker Compose.

### 1. Prerequisites
- Docker & Docker Compose installed on your server.
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather)).
- A Google Gemini API Key (from [Google AI Studio](https://aistudio.google.com/)).
- Your Telegram User ID (to access the admin panel).

### 2. Installation

Clone the repository:
```bash
git clone https://github.com/Yaroslavlazarenko/GeminiBot.git
cd GeminiBot
```

Configure your environment variables:
```bash
cp .env.example .env
nano .env
```

**Required `.env` fields:**
```env
BOT_TOKEN=1234567890:YOUR_TELEGRAM_BOT_TOKEN
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
ADMIN_TELEGRAM_ID=YOUR_PERSONAL_TELEGRAM_ID # e.g., 123456789

# Optional: For voice message generation
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
```

### 3. Run the Bot
```bash
docker compose up -d --build
```
This will start two containers: `mongodb` and the `bot`.

---

## 🔐 Web Admin Panel

Mia includes an embedded Web UI (running on port `8081` by default) to manage the bot dynamically without restarting Docker.

### How to access:
1. Ensure your `ADMIN_TELEGRAM_ID` is set in `.env`.
2. Send the command `/admin` to Mia in Telegram.
3. The bot will generate a secure, one-time access link:
   `http://<YOUR_SERVER_IP>:8081/?token=SECRET_HASH`
4. Click the link. The browser will securely authenticate you via HTTP cookies.

### What you can configure:
- **System Instruction:** Edit Mia's persona, backstory, and behavioral rules.
- **Models:** Change the Gatekeeper or Persona models on the fly.
- **Gemini Base URL:** Override the default Google API endpoint (e.g., to route traffic through a custom proxy).
- **MCP Servers Config:** Connect remote MCP servers using a JSON configuration.

#### MCP Servers Config Example:
```json
{
  "math_server": {
    "url": "https://mathematics.fastmcp.app/mcp"
  },
  "search_server": {
    "url": "https://mcp.exa.ai/mcp",
    "type": "sse",
    "headers": {
      "Authorization": "Bearer YOUR_API_KEY"
    }
  }
}
```
*When you click "Save" in the panel, the bot will dynamically reconnect to the new MCP servers, fetch their schemas, and inject them into Mia's brain without dropping a single Telegram message.*

---

## 🧠 Architecture Overview

The project follows a Pragmatic Clean Architecture approach:
- **`core/`**: Centralized configurations, database connection (`motor`), enums, and logging.
- **`services/`**:
  - `ai_service.py`: Orchestrates the main Gemini model and the autonomous MCP tool execution loop.
  - `gatekeeper_service.py`: Evaluates incoming context and decides `RESPOND`, `IGNORE`, or `DISABLE_RESPONSES`.
  - `mcp_manager.py`: Handles dynamic HTTP/SSE connections to remote MCP servers and collision resolution.
  - `tts_service.py`: Generates audio bytes via ElevenLabs.
- **`bot/`**: Telegram presentation layer. `handlers.py` maps Telegram events to the unified `ChatContext` abstraction.

---

## 🙏 Acknowledgments

Special thanks to the [GeminiMCPRelay](https://github.com/Kirillka999/GeminiMCPRelay) project by [Kirillka999](https://github.com/Kirillka999). The core MCP connection manager and autonomous `function_calling` orchestration loop used in Mia were heavily inspired by and adapted from their excellent relay architecture!

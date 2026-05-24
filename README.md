[🇬🇧 English](README.md) | [🇷🇺 Русский](README.ru.md) | [🇺🇦 Українська](README.uk.md)

# 🧠 Mia - Advanced Multimodal Telegram AI Assistant

Mia is a highly sophisticated, persona-driven Telegram bot built on the **Google Gemini API** (GenAI SDK) and **FastMCP**. Designed to feel like a real conversational partner, Mia possesses long-term memory, can see and analyze photos, watch video notes, listen to voice messages, and reply with real voice audio. 

Under the hood, Mia uses a modern, containerized architecture with MongoDB, featuring a dual-model "Gatekeeper" routing system, structured output thought-isolation, and seamless bridging to external MCP (Model Context Protocol) microservices.

---

## ✨ Key Features

### 🛡️ Dual-Model Gatekeeper (Group Chat Optimization)
Running an AI in a busy group chat can be expensive and spammy. Mia solves this using a two-tier model approach:
- **The Gatekeeper (`gemini-3.1-flash-lite`):** A fast, cheap model that reads every incoming group message. It evaluates the conversational context, searches chat history if needed, and decides whether Mia should *Respond*, *Ignore*, or *Disable* herself temporarily.
- **The Persona (`gemini-3.5-flash`):** The heavier, smarter model that generates Mia's actual responses only when the Gatekeeper approves the interaction.

### 🤫 Structured Thought Isolation (Zero Prompt Leaks)
Mia generates responses using **Structured Outputs (JSON Schema)**. She is forced to separate her internal reasoning, image analysis, and drafts into a hidden `internal_monologue` field. Only the polished `message` field is routed to Telegram. This completely eliminates "prompt leaking" (e.g., the AI accidentally sending `/thought` or raw analytical metadata into the chat).

### 👁️ Multimodality: Vision, Voice, and Video
- **Video Notes & Audio:** Automatically transcribes voice messages and video notes ("кружочки") using the ultra-fast **Groq Whisper API**. Video notes are also run through Gemini Vision for inline visual analysis.
- **Cascading Image Compression:** When receiving massive photos, the bot gracefully cascades compressions (2K -> 1080p -> 720p). This ensures the highest possible quality is sent to the LLM without ever exceeding the 4.5MB context limit.
- **Sticker Catalog Vision:** Background workers download your configured Telegram sticker packs, run them through Gemini Vision to generate emotional/visual descriptions, and cache them in the DB. Mia then uses a local tool to pick and send the perfect sticker.
- **Real Voice Replies:** Mia can send authentic voice messages using the **ElevenLabs TTS API**.

### 🧠 Persistent Memory & Context
- **Immutable History:** Full chat logs are stored in MongoDB. Mia can actively use tools to search past conversations by keywords or specific dates.
- **Isolated User Memory:** Mia proactively observes and saves user facts, preferences, and traits. Strict programmatic privacy boundaries ensure that sensitive facts learned in a private chat never leak into public groups.
- **Contextual Awareness:** Intercepts Telegram's native `reply` and `forward` attributes, injecting exact speaker attributions so the AI perfectly understands complex conversational flows.

### 🔌 MCP Extensibility & Media Bridging
Extend Mia's capabilities endlessly without modifying her core code:
- **Local Native Tools:** Mia autonomously reacts to messages, replies to specific quotes, searches/sends stickers, and saves memory facts.
- **Shared Volume Media Bridging:** Telegram files exist in RAM. If an external MCP server (like Google Reverse Image Search) requires an absolute file path, Mia organically discovers the `download_media_to_disk` tool, saves the image to a shared Docker volume (with automatic 1-hour cleanup), and passes the path to the external microservice seamlessly.
- **Remote MCP Support:** Connect to external HTTP/SSE MCP servers via the Admin Panel. The bot dynamically maps and orchestrates remote tools (like Exa Search) autonomously.

---

## 🏗 Architecture Overview

1. **Dockerized Environment:** The main bot runs in a Python 3.11 container. MongoDB runs in an adjacent container.
2. **Shared Media Volume:** A dedicated Docker volume (`gemini_shared_media`) allows the bot to securely drop media payloads for adjacent external MCP bridges (e.g., Node.js tools requiring local filesystem access).
3. **Web Admin Panel:** A secure FastAPI web interface running on port `8081`. Access is strictly protected via one-time login tokens generated dynamically in Telegram by the bot owner. Allows real-time editing of system prompts, MCP JSON configurations, and API keys without restarting the bot.

---

## ⚙️ Prerequisites

- **Docker** and **Docker Compose** installed on your host.
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather).
- A Google Gemini API Key from [Google AI Studio](https://aistudio.google.com/).
- *(Optional)* Groq API Key for fast voice transcription.
- *(Optional)* ElevenLabs API Key for voice generation.

---

## 🚀 Installation & Quick Start

### 1. Clone & Configure
```bash
git clone https://github.com/Yaroslavlazarenko/GeminiBot.git
cd GeminiBot
cp .env.example .env
```

Open `.env` and fill in your credentials:
```env
BOT_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
ADMIN_TELEGRAM_ID=your_telegram_user_id # Essential for accessing the Admin Panel
# Optional:
GROQ_API_KEY=your_groq_api_key
ELEVENLABS_API_KEY=your_elevenlabs_api_key
```

### 2. Build & Launch
```bash
docker-compose up -d --build
```
The bot will start, connect to MongoDB, build its database indexes, and begin polling Telegram.

### 3. Access the Secure Admin Panel
1. Send `/admin` to your bot in Telegram.
2. If your Telegram ID matches `ADMIN_TELEGRAM_ID`, the bot will reply with a secure, one-time-use link.
3. Open the link in your browser to configure:
   - System Prompts (Mia's personality).
   - Additional API Keys.
   - Target Sticker Packs.
   - External MCP Server endpoints.

---

## 🤖 Interacting with Mia

- **Private Chats:** Simply send her a message, photo, or voice note. Mia processes bursts of messages (sent within 3 seconds) into a single thought context to avoid spamming you back.
- **Group Chats:** Add Mia to a group. **Important:** For Mia to read all messages (so the Gatekeeper can analyze context), you must either make her an Admin or disable Group Privacy Mode via @BotFather.
- **Commands:**
  - `/clear` - Wipes the bot's short-term context window (but retains long-term DB memory).
  - `/admin` - Generates a login link for the Web Panel (owner only).

---

## 🛠 Adding External MCP Servers (SSE)

You can plug in third-party MCP servers (like web search, GitHub integration, or Reverse Image Search) seamlessly.

1. Spin up an external MCP server bridging to SSE (e.g., using `@modelcontextprotocol/inspector`) on the same host machine.
2. If the MCP server needs to analyze images sent to Telegram, ensure it mounts the `geminibot_gemini_shared_media` volume to `/tmp/gemini_media`.
3. Open the Mia Admin Panel and add the server to the **MCP Config** JSON:
```json
{
  "google_images": {
    "url": "http://mcp-images-standalone:5173/sse"
  },
  "exa_search": {
    "url": "https://mcp.exa.ai/mcp",
    "headers": {"Authorization": "Bearer YOUR_EXA_API_KEY"}
  }
}
```
Mia will instantly inherit the new capabilities on her very next conversational turn.

---

## 📄 License
This project is licensed under the MIT License.
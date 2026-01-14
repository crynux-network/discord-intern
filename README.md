# Community Intern

Community Intern is an AI and LLM powered Discord FAQ assistant that monitors selected channels, detects questions, and posts helpful answers in newly created threads to keep the main channel clean.

## What it does

- Watches all readable Discord channels for question-like messages
- Uses AI to decide whether a message is a question and whether it is in scope to answer (and skips messages that are not)
- Uses an LLM to draft a helpful answer grounded in the knowledge base (local files and configured web sources) when it decides to respond
- Creates a thread from the triggering message and replies inside that thread
- Supports follow-up questions by replying again when a thread continues (using the full thread context)

## Key features


- **AI-generated, source-grounded answers**: An LLM generates answers from your documentation sources and can include citations back to those sources.
- **Knowledge base from files and links**: Uses a local folder of text sources and can incorporate relevant web pages referenced by links (supports dynamic content loading).
- **Bring your own LLM**: Choose which LLM provider and model to use via configuration.
- **Thread-first replies**: Answers live in message-backed threads rather than cluttering the channel.
- **Configurable scope**: Communities can tune what kinds of questions are considered answerable without changing code.

## Documentation

See `docs/` for architecture and module-level documentation, plus configuration guidance.

## Get Started

### 1) Create a Discord bot and enable message content intent

- Create an application + bot in the Discord Developer Portal.
- Enable **Message Content Intent** for the bot (required to read message text).
- Invite/install the bot to your server **without** requesting **View Channels** (and without **Administrator**). The bot should start with no channel visibility by default.
- After installation, Discord will create a role for the bot (for this project: **Community Intern**).
- To allow the bot to operate in a specific channel, grant the **Community Intern** role channel permissions:
  - **View Channel**
  - **Read Message History**
  - **Create Public Threads** (and/or **Create Private Threads**, depending on your usage)
  - **Send Messages in Threads**

### 2) Install dependencies

```bash
$ python -m venv venv
$ ./venv/bin/activate
(venv) $ pip install -r requirements.txt
(venv) $ pip install .
```

### 3) Configure the application

**a) Create `data/config/config.yaml`**

Start from `examples/config.yaml` and copy it to `data/config/config.yaml`.

```yaml
# Any OpenAI-compatible chat completion API could be used
ai:
  llm_base_url: "https://bridge.crynux-as.xyz/v1/llm"
  llm_model: "Qwen/Qwen2.5-7B"
```

**b) Create `.env` for secrets**

Create a `.env` file in the root directory (same level as `pyproject.toml`) to store sensitive keys.

```bash
APP__DISCORD__TOKEN=your_discord_bot_token
APP__AI__LLM_API_KEY=your_llm_api_key
```

Notes:

- Environment variables in `.env` override values in `config.yaml` using the `APP__` prefix (e.g., `APP__DISCORD__TOKEN` overrides `discord.token`).

### 4) Setup Knowledge Base Sources

Add your documentation to the knowledge base so the bot can answer questions.

- **Local Files**: Place text files (Markdown, .txt, etc.) in the `data/knowledge-base/sources/` directory.
- **Web Links**: List URLs in `data/knowledge-base/links.txt` (one URL per line). The bot will fetch and index the content of these pages.

### 5) Initialize Knowledge Base

Before running the bot, initialize the knowledge base index. This will scan your sources folder and fetch any web links.

```bash
(venv) $ python -m community_intern init_kb
```

### 6) Run the bot

This project currently ships with a mock AI client that always replies with a fixed message. This lets you validate Discord connectivity, routing, and thread creation before implementing the full AI module.

```bash
(venv) $ python -m community_intern run
```

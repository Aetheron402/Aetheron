import requests
from typing import Dict, Any
from openai import OpenAI


# Main AI Reply Generator
def generate_ai_reply(message: str, ai_config: Dict[str, Any], logger) -> str:
    """
    Generates an AI reply using the configured provider.
    Falls back to a simple non-AI message if disabled or an error occurs.
    """

    if not ai_config.get("enabled", False):
        return generate_fallback_reply(message)

    provider = ai_config.get("provider", "openai").lower()

    try:
        if provider == "openai":
            return ai_openai(message, ai_config, logger)

        if provider == "anthropic":
            return ai_anthropic(message, ai_config, logger)

        if provider == "openrouter":
            return ai_openrouter(message, ai_config, logger)

        if provider == "custom":
            return ai_custom(message, ai_config, logger)

        logger.warning(f"Unknown AI provider '{provider}', using fallback reply.")
        return generate_fallback_reply(message)

    except Exception as e:
        logger.error(f"AI generation error: {e}")
        return generate_fallback_reply(message)


# OpenAI Provider (Default)
def ai_openai(message: str, ai_config: Dict[str, Any], logger) -> str:
    """
    Generates a reply using OpenAI's Chat Completions API.
    """
    api_key = ai_config.get("openai_api_key", "")
    client = OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=ai_config.get("model", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "You are a helpful Discord support assistant."},
                {"role": "user", "content": message}
            ]
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return generate_fallback_reply(message)


# Anthropic Provider (Real HTTP Logic)
def ai_anthropic(message: str, ai_config: Dict[str, Any], logger) -> str:
    """
    Generates a reply using Anthropic's Claude API (requires API key).
    """
    api_key = ai_config.get("anthropic_api_key", "")
    model = ai_config.get("model", "claude-3-haiku-20240307")

    if not api_key:
        logger.error("Anthropic provider selected but no API key provided.")
        return generate_fallback_reply(message)

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": message}
                ],
                "max_tokens": 300
            },
            timeout=10
        )

        data = response.json()
        return data["content"][0]["text"]

    except Exception as e:
        logger.error(f"Anthropic API error: {e}")
        return generate_fallback_reply(message)


# OpenRouter Provider (Real HTTP Logic)
def ai_openrouter(message: str, ai_config: Dict[str, Any], logger) -> str:
    """
    Generates a reply using OpenRouter API (requires API key).
    """
    api_key = ai_config.get("openrouter_api_key", "")
    model = ai_config.get("model", "gpt-4o-mini")  # Any OpenRouter model name

    if not api_key:
        logger.error("OpenRouter provider selected but no API key provided.")
        return generate_fallback_reply(message)

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful Discord support assistant."},
                    {"role": "user", "content": message}
                ]
            },
            timeout=10
        )

        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    except Exception as e:
        logger.error(f"OpenRouter API error: {e}")
        return generate_fallback_reply(message)


# Custom Provider (Generic POST)
def ai_custom(message: str, ai_config: Dict[str, Any], logger) -> str:
    """
    Sends the message to a user-defined LLM endpoint.
    The user must specify:
      - custom_api_url
      - custom_api_key (optional)
      - model (optional)
    """
    url = ai_config.get("custom_api_url")
    key = ai_config.get("custom_api_key")
    model = ai_config.get("model", "default")

    if not url:
        logger.error("Custom AI provider selected but no 'custom_api_url' provided.")
        return generate_fallback_reply(message)

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        response = requests.post(
            url,
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": message}
                ]
            },
            timeout=10
        )

        data = response.json()
        return data.get("reply") or data.get("message") or str(data)

    except Exception as e:
        logger.error(f"Custom AI provider error: {e}")
        return generate_fallback_reply(message)


# Fallback non-AI reply (real, clean)
def generate_fallback_reply(message: str) -> str:
    """
    Message sent when AI is disabled or an error occurs.
    """
    return f"Thanks for your message! I’m here to help.\nType !help to see what I can do."

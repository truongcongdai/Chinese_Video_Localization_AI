#!/usr/bin/env python3
"""
Generate .env file from .env.example with random secrets.
This script creates a .env file with secure random values for sensitive fields
while preserving placeholder values that users need to fill in manually.
Run this script from the dist/ folder to set up your environment.
"""

import secrets
import os
import sys
from pathlib import Path


def generate_random_hex(length=32):
    """Generate a random hex string of specified length."""
    return secrets.token_hex(length)


def generate_random_string(length=24):
    """Generate a random alphanumeric string."""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main():
    # Paths - script is in dist/, .env.example is in parent
    dist_dir = Path(__file__).parent
    project_root = dist_dir.parent
    env_example = project_root / ".env.example"
    env_file = dist_dir / ".env"

    # Check if .env.example exists
    if not env_example.exists():
        print(f"Error: {env_example} not found")
        print("Please run this script from the dist/ folder")
        sys.exit(1)

    # Read .env.example
    with open(env_example, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Fields to generate random values for
    random_fields = {
        "WEB_SESSION_SECRET": lambda: generate_random_hex(32),
        "ADMIN_API_TOKEN": lambda: generate_random_string(32),
    }

    # Fields to keep as placeholders (user must fill)
    placeholder_fields = {
        "GEMINI_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "FACEBOOK_APP_ID": "",
        "FACEBOOK_APP_SECRET": "",
        "TIKTOK_CLIENT_KEY": "",
        "TIKTOK_CLIENT_SECRET": "",
        "GITHUB_CLIENT_ID": "",
        "GITHUB_CLIENT_SECRET": "",
        "SMTP_EMAIL": "your_email@gmail.com",
        "SMTP_PASSWORD": "your_app_password_here",
        "TELEGRAM_BOT_TOKEN": "your_telegram_bot_token_here",
    }

    # Process lines
    output_lines = []
    for line in lines:
        line = line.rstrip()
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            # Generate random value for specific fields
            if key in random_fields:
                value = random_fields[key]()
                output_lines.append(f"{key}={value}")
            # Keep placeholder for user to fill
            elif key in placeholder_fields:
                output_lines.append(f"{key}={placeholder_fields[key]}")
            # Keep original value
            else:
                output_lines.append(line)
        else:
            output_lines.append(line)

    # Write to dist/.env
    with open(env_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"✓ Generated .env file at {env_file}")
    print("\n" + "="*60)
    print("IMPORTANT: Please edit .env and fill in the following:")
    print("="*60)
    print("• SMTP_EMAIL - Your Gmail address")
    print("• SMTP_PASSWORD - Your Gmail App Password")
    print("• TELEGRAM_BOT_TOKEN - Your Telegram bot token (optional)")
    print("• GEMINI_API_KEY - For AI features (optional)")
    print("• OPENROUTER_API_KEY - For AI features (optional)")
    print("• GOOGLE_CLIENT_ID/SECRET - For YouTube OAuth (optional)")
    print("• FACEBOOK_APP_ID/SECRET - For Facebook OAuth (optional)")
    print("• TIKTOK_CLIENT_KEY/SECRET - For TikTok OAuth (optional)")
    print("="*60)
    print("\nAfter filling in the values, run:")
    print("  python run_web.py")


if __name__ == "__main__":
    main()

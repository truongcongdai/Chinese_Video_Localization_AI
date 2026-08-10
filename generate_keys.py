#!/usr/bin/env python3
"""
Generate RSA key pair for license system
Run this script to generate private/public keys for license encryption/decryption

Usage:
    python generate_keys.py

Output:
    - Private key: Save to .env as LICENSE_PRIVATE_KEY (admin only)
    - Public key: Save to .env as LICENSE_PUBLIC_KEY (client side)
"""

import sys
from pathlib import Path

# Add src to path
REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from universal_video_ai.license import LicenseCrypto
except ImportError:
    print("ERROR: Cannot import license module. Make sure you're in the project root.")
    sys.exit(1)


def main():
    print("=" * 60)
    print("Generate RSA Key Pair for License System")
    print("=" * 60)
    print()
    
    print("Generating RSA 2048-bit key pair...")
    try:
        private_key, public_key = LicenseCrypto.generate_key_pair()
    except Exception as e:
        print(f"ERROR: Failed to generate keys: {e}")
        sys.exit(1)
    
    print()
    print("=" * 60)
    print("PRIVATE KEY (ADMIN ONLY - KEEP SECRET!)")
    print("=" * 60)
    print()
    print(private_key)
    print()
    print("=" * 60)
    print("PUBLIC KEY (CLIENT SIDE)")
    print("=" * 60)
    print()
    print(public_key)
    print()
    print("=" * 60)
    print("INSTRUCTIONS")
    print("=" * 60)
    print()
    print("1. Copy PRIVATE KEY and paste into .env as LICENSE_PRIVATE_KEY")
    print("2. Copy PUBLIC KEY and paste into .env as LICENSE_PUBLIC_KEY")
    print("3. IMPORTANT: Never share PRIVATE KEY with anyone!")
    print("4. PUBLIC KEY can be distributed to clients")
    print()
    print("Example .env configuration:")
    print()
    print("LICENSE_ENABLED=true")
    print("LICENSE_PRIVATE_KEY=<paste_private_key_here>")
    print("LICENSE_PUBLIC_KEY=<paste_public_key_here>")
    print("LICENSE_FILE_PATH=./local_data/license.key")
    print("LICENSE_HARDWARE_BINDING=false")
    print()
    print("=" * 60)
    print("Keys generated successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()

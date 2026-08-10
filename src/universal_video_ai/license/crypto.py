# src/universal_video_ai/license/crypto.py
"""
Cryptographic functions for license encryption/decryption
Uses RSA for key exchange and AES for data encryption
"""

import base64
import json
import secrets
from datetime import datetime
from typing import Dict, Any, Optional

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False


class LicenseCrypto:
    """Handles license encryption and decryption"""
    
    def __init__(self, private_key_pem: Optional[str] = None, public_key_pem: Optional[str] = None):
        """
        Initialize crypto with RSA keys
        
        Args:
            private_key_pem: PEM-encoded private key (for admin/encryption)
            public_key_pem: PEM-encoded public key (for client/decryption)
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise ImportError("cryptography library is required. Install with: pip install cryptography")
        
        self.private_key = None
        self.public_key = None
        
        if private_key_pem:
            self.private_key = serialization.load_pem_private_key(
                private_key_pem.encode(),
                password=None,
                backend=default_backend()
            )
        
        if public_key_pem:
            self.public_key = serialization.load_pem_public_key(
                public_key_pem.encode(),
                backend=default_backend()
            )
    
    @staticmethod
    def generate_key_pair() -> tuple[str, str]:
        """
        Generate new RSA key pair
        
        Returns:
            (private_key_pem, public_key_pem)
        """
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ).decode()
        
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        
        return private_pem, public_pem
    
    def encrypt_license(self, license_data: Dict[str, Any]) -> str:
        """
        Encrypt license data using RSA + AES
        
        Args:
            license_data: Dictionary containing license information
            
        Returns:
            Base64-encoded encrypted license string
        """
        if not self.private_key:
            raise ValueError("Private key required for encryption")
        
        # Convert to JSON
        json_data = json.dumps(license_data, separators=(',', ':')).encode()
        
        # Generate AES key and IV
        aes_key = secrets.token_bytes(32)  # 256-bit key
        aes_iv = secrets.token_bytes(16)   # 128-bit IV
        
        # Encrypt data with AES
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv), backend=default_backend())
        encryptor = cipher.encryptor()
        
        # Pad data to block size
        pad_length = 16 - (len(json_data) % 16)
        padded_data = json_data + bytes([pad_length] * pad_length)
        
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        
        # Encrypt AES key with RSA
        encrypted_aes_key = self.private_key.public_key().encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        # Combine: [encrypted_aes_key_length][encrypted_aes_key][aes_iv][encrypted_data]
        result = (
            len(encrypted_aes_key).to_bytes(4, 'big') +
            encrypted_aes_key +
            aes_iv +
            encrypted_data
        )
        
        return base64.b64encode(result).decode()
    
    def decrypt_license(self, encrypted_license: str) -> Dict[str, Any]:
        """
        Decrypt license string
        
        Args:
            encrypted_license: Base64-encoded encrypted license string
            
        Returns:
            Dictionary containing license information
        """
        if not self.public_key:
            raise ValueError("Public key required for decryption")
        
        # Decode base64
        data = base64.b64decode(encrypted_license.encode())
        
        # Extract components
        aes_key_length = int.from_bytes(data[:4], 'big')
        encrypted_aes_key = data[4:4 + aes_key_length]
        aes_iv = data[4 + aes_key_length:4 + aes_key_length + 16]
        encrypted_data = data[4 + aes_key_length + 16:]
        
        # Decrypt AES key with RSA
        aes_key = self.public_key.decrypt(
            encrypted_aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        # Decrypt data with AES
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv), backend=default_backend())
        decryptor = cipher.decryptor()
        
        padded_data = decryptor.update(encrypted_data) + decryptor.finalize()
        
        # Remove padding
        pad_length = padded_data[-1]
        json_data = padded_data[:-pad_length]
        
        return json.loads(json_data.decode())

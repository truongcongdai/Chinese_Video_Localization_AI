# src/universal_video_ai/license/__init__.py
"""
License Management System for Chinese Video Localization AI

Features:
- Hardware fingerprinting (optional binding)
- Encrypted license keys (RSA + AES)
- Token-based trial system
- Expiration date control
- Admin UI integration
"""

from .manager import LicenseManager, LicenseInfo, LicenseValidationError
from .fingerprint import get_hardware_fingerprint
from .crypto import LicenseCrypto

__all__ = [
    'LicenseManager',
    'LicenseInfo',
    'LicenseValidationError',
    'get_hardware_fingerprint',
    'LicenseCrypto',
]

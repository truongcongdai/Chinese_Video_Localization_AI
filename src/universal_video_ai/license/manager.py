# src/universal_video_ai/license/manager.py
"""
License Manager - Main license validation and management logic
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

from .crypto import LicenseCrypto
from .fingerprint import get_hardware_fingerprint

logger = logging.getLogger(__name__)


class LicenseValidationError(Exception):
    """License validation failed"""
    pass


@dataclass
class LicenseInfo:
    """License information structure"""
    user_id: str
    user_name: str
    user_email: str
    license_type: str  # "trial", "monthly", "lifetime"
    expiration_date: str  # ISO format
    token_limit: int  # 0 = unlimited
    tokens_used: int
    hardware_binding: Optional[str] = None  # Hardware fingerprint if binding enabled
    created_date: str = ""
    notes: str = ""
    enabled_features: List[str] = None  # List of enabled features (empty = all features)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LicenseInfo':
        return cls(**data)
    
    def is_expired(self) -> bool:
        """Check if license is expired"""
        if self.license_type == "lifetime":
            return False
        try:
            exp_date = datetime.fromisoformat(self.expiration_date)
            return datetime.now() > exp_date
        except Exception:
            return True
    
    def is_token_limit_reached(self) -> bool:
        """Check if token limit is reached"""
        if self.token_limit == 0:  # Unlimited
            return False
        return self.tokens_used >= self.token_limit
    
    def days_remaining(self) -> int:
        """Get days remaining until expiration"""
        if self.license_type == "lifetime":
            return 999999
        try:
            exp_date = datetime.fromisoformat(self.expiration_date)
            remaining = exp_date - datetime.now()
            return max(0, remaining.days)
        except Exception:
            return 0
    
    def tokens_remaining(self) -> int:
        """Get remaining tokens"""
        if self.token_limit == 0:
            return 999999
        return max(0, self.token_limit - self.tokens_used)
    
    def has_feature_access(self, feature: str) -> bool:
        """Check if license has access to a specific feature"""
        # If enabled_features is None or empty, all features are accessible
        if not self.enabled_features:
            return True
        return feature in self.enabled_features
    
    def get_enabled_features(self) -> List[str]:
        """Get list of enabled features"""
        return self.enabled_features or []


class LicenseManager:
    """Main license management class"""
    
    def __init__(
        self,
        public_key_pem: Optional[str] = None,
        private_key_pem: Optional[str] = None,
        license_file_path: Optional[Path] = None,
        enable_hardware_binding: bool = False
    ):
        """
        Initialize license manager
        
        Args:
            public_key_pem: Public key for decryption (client side)
            private_key_pem: Private key for encryption (admin side)
            license_file_path: Path to license file
            enable_hardware_binding: Whether to bind license to hardware
        """
        self.crypto = LicenseCrypto(private_key_pem, public_key_pem)
        self.license_file_path = license_file_path or Path("license.key")
        self.enable_hardware_binding = enable_hardware_binding
        self._cached_license: Optional[LicenseInfo] = None
    
    def create_license(
        self,
        user_id: str,
        user_name: str,
        user_email: str,
        license_type: str,
        duration_days: int,
        token_limit: int = 0,
        bind_to_hardware: bool = False,
        notes: str = "",
        enabled_features: Optional[List[str]] = None
    ) -> str:
        """
        Create a new license key (admin function)
        
        Args:
            user_id: Unique user identifier
            user_name: User's name
            user_email: User's email
            license_type: Type of license (trial, monthly, lifetime)
            duration_days: Duration in days (ignored for lifetime)
            token_limit: Token limit (0 = unlimited)
            bind_to_hardware: Whether to bind to current hardware
            notes: Additional notes
            enabled_features: List of enabled features (None/empty = all features)
            
        Returns:
            Encrypted license key string
        """
        # Calculate expiration date
        if license_type == "lifetime":
            expiration_date = "2099-12-31T23:59:59"
        else:
            exp_date = datetime.now() + timedelta(days=duration_days)
            expiration_date = exp_date.isoformat()
        
        # Get hardware fingerprint if binding enabled
        hardware_binding = None
        if bind_to_hardware:
            hardware_binding = get_hardware_fingerprint()
        
        # Create license info
        license_info = LicenseInfo(
            user_id=user_id,
            user_name=user_name,
            user_email=user_email,
            license_type=license_type,
            expiration_date=expiration_date,
            token_limit=token_limit,
            tokens_used=0,
            hardware_binding=hardware_binding,
            created_date=datetime.now().isoformat(),
            notes=notes,
            enabled_features=enabled_features
        )
        
        # Encrypt and return
        return self.crypto.encrypt_license(license_info.to_dict())
    
    def validate_license(self, license_key: Optional[str] = None) -> LicenseInfo:
        """
        Validate license key
        
        Args:
            license_key: License key to validate (uses file if not provided)
            
        Returns:
            LicenseInfo object
            
        Raises:
            LicenseValidationError: If license is invalid
        """
        # Load license from file if not provided
        if license_key is None:
            if not self.license_file_path.exists():
                raise LicenseValidationError("License file not found")
            license_key = self.license_file_path.read_text().strip()
        
        # Decrypt license
        try:
            license_data = self.crypto.decrypt_license(license_key)
        except Exception as e:
            raise LicenseValidationError(f"Failed to decrypt license: {str(e)}")
        
        # Parse license info
        try:
            license_info = LicenseInfo.from_dict(license_data)
        except Exception as e:
            raise LicenseValidationError(f"Invalid license format: {str(e)}")
        
        # Check expiration
        if license_info.is_expired():
            raise LicenseValidationError(
                f"License expired on {license_info.expiration_date}. "
                f"Please renew your license."
            )
        
        # Check hardware binding
        if self.enable_hardware_binding and license_info.hardware_binding:
            current_fingerprint = get_hardware_fingerprint()
            if current_fingerprint != license_info.hardware_binding:
                raise LicenseValidationError(
                    "License is bound to a different machine. "
                    "Please contact support for a new license."
                )
        
        # Cache validated license
        self._cached_license = license_info
        
        return license_info
    
    def save_license(self, license_key: str) -> None:
        """
        Save license key to file
        
        Args:
            license_key: Encrypted license key
        """
        self.license_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.license_file_path.write_text(license_key)
        logger.info(f"License saved to {self.license_file_path}")
    
    def load_license(self) -> Optional[LicenseInfo]:
        """
        Load and validate license from file
        
        Returns:
            LicenseInfo if valid, None if file doesn't exist
        """
        if not self.license_file_path.exists():
            return None
        
        try:
            return self.validate_license()
        except LicenseValidationError as e:
            logger.warning(f"License validation failed: {str(e)}")
            return None
    
    def use_token(self, count: int = 1) -> bool:
        """
        Consume tokens from license
        
        Args:
            count: Number of tokens to consume
            
        Returns:
            True if successful, False if limit reached
        """
        if self._cached_license is None:
            self.load_license()
        
        if self._cached_license is None:
            return False
        
        if self._cached_license.is_token_limit_reached():
            return False
        
        self._cached_license.tokens_used += count
        
        # Update license file
        if self.license_file_path.exists():
            license_key = self.license_file_path.read_text().strip()
            license_data = self.crypto.decrypt_license(license_key)
            license_data['tokens_used'] = self._cached_license.tokens_used
            new_key = self.crypto.encrypt_license(license_data)
            self.save_license(new_key)
        
        return True
    
    def get_license_status(self) -> Dict[str, Any]:
        """
        Get current license status
        
        Returns:
            Dictionary with license status information
        """
        license_info = self.load_license()
        
        if license_info is None:
            return {
                "valid": False,
                "message": "No license found"
            }
        
        return {
            "valid": True,
            "user_id": license_info.user_id,
            "user_name": license_info.user_name,
            "user_email": license_info.user_email,
            "license_type": license_info.license_type,
            "expiration_date": license_info.expiration_date,
            "days_remaining": license_info.days_remaining(),
            "token_limit": license_info.token_limit,
            "tokens_used": license_info.tokens_used,
            "tokens_remaining": license_info.tokens_remaining(),
            "hardware_binding": license_info.hardware_binding is not None,
            "notes": license_info.notes
        }

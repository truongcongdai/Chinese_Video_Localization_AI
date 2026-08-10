# src/universal_video_ai/license/fingerprint.py
"""
Hardware fingerprinting for license binding
Collects unique hardware identifiers to bind license to specific machine
"""

import hashlib
import platform
import subprocess
import uuid
from typing import Optional


def get_hardware_fingerprint() -> str:
    """
    Generate a unique hardware fingerprint for the current machine
    
    Returns:
        SHA256 hash of combined hardware identifiers
    """
    identifiers = []
    
    # Machine ID (Windows: MachineGuid, Linux: machine-id, Mac: IOPlatformUUID)
    machine_id = _get_machine_id()
    if machine_id:
        identifiers.append(machine_id)
    
    # CPU info
    cpu_info = _get_cpu_info()
    if cpu_info:
        identifiers.append(cpu_info)
    
    # MAC address of first network interface
    mac = _get_mac_address()
    if mac:
        identifiers.append(mac)
    
    # Combine all identifiers
    combined = "|".join(identifiers).encode()
    
    # Return SHA256 hash
    return hashlib.sha256(combined).hexdigest()


def _get_machine_id() -> Optional[str]:
    """Get unique machine ID based on OS"""
    system = platform.system()
    
    try:
        if system == "Windows":
            # Windows MachineGuid from registry
            import winreg
            key = winreg.HKEY_LOCAL_MACHINE
            subkey = r"SOFTWARE\Microsoft\Cryptography"
            with winreg.OpenKey(key, subkey) as reg_key:
                machine_guid, _ = winreg.QueryValueEx(reg_key, "MachineGuid")
                return machine_guid
        elif system == "Linux":
            # Linux machine-id
            if Path("/etc/machine-id").exists():
                return Path("/etc/machine-id").read_text().strip()
            elif Path("/var/lib/dbus/machine-id").exists():
                return Path("/var/lib/dbus/machine-id").read_text().strip()
        elif system == "Darwin":
            # Mac IOPlatformUUID
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "IOPlatformUUID" in line:
                        return line.split('"')[-2]
    except Exception:
        pass
    
    return None


def _get_cpu_info() -> Optional[str]:
    """Get CPU identifier"""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["wmic", "cpu", "get", "ProcessorId"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()
        else:
            # Use CPU model and core count
            cpu_model = platform.processor()
            cpu_count = str(os.cpu_count())
            return f"{cpu_model}_{cpu_count}"
    except Exception:
        pass
    
    return None


def _get_mac_address() -> Optional[str]:
    """Get MAC address of first network interface"""
    try:
        mac = uuid.getnode()
        return ":".join(f"{(mac >> elements) & 0xff:02x}" for elements in range(5, -1, -1))
    except Exception:
        pass
    
    return None


# Import Path for Linux machine-id check
from pathlib import Path
import os

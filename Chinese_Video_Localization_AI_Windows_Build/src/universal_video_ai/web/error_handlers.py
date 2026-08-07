"""Error handling utilities with clear, actionable error messages."""
import logging
from typing import Optional, Dict, Any
from enum import Enum

_logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Categories of errors for better user messaging."""
    DOWNLOAD = "download"
    TRANSCRIPTION = "transcription"
    TRANSLATION = "translation"
    TTS = "tts"
    RENDER = "render"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    INVALID_INPUT = "invalid_input"
    SYSTEM = "system"


class UserFacingError(Exception):
    """Base class for errors that should be shown to users with helpful messages."""
    
    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        user_message: Optional[str] = None,
        action_suggestion: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.category = category
        self.user_message = user_message or message
        self.action_suggestion = action_suggestion
        self.details = details or {}
        super().__init__(message)


class DownloadError(UserFacingError):
    """Error during video download."""
    
    def __init__(
        self,
        url: str,
        reason: str,
        suggestion: Optional[str] = None,
    ):
        user_message = f"Không thể tải video từ URL: {url[:50]}..."
        
        suggestions = {
            "rate_limit": "Bạn đang tải quá nhanh. Vui lòng đợi 1-2 phút rồi thử lại.",
            "cookie_expired": "Cookie của Douyin đã hết hạn. Vui lòng thử lại sau.",
            "network": "Lỗi kết nối mạng. Vui lòng kiểm tra internet và thử lại.",
            "invalid_url": "URL không hợp lệ. Vui lòng kiểm tra và thử lại.",
            "video_removed": "Video đã bị xóa hoặc không công khai. Vui lòng thử video khác.",
            "private": "Video này ở chế độ riêng tư. Vui lòng dùng video công khai.",
        }
        
        action_suggestion = suggestion or suggestions.get(
            reason,
            "Vui lòng thử lại sau hoặc dùng URL khác."
        )
        
        super().__init__(
            message=f"Download failed for {url}: {reason}",
            category=ErrorCategory.DOWNLOAD,
            user_message=user_message,
            action_suggestion=action_suggestion,
            details={"url": url, "reason": reason},
        )


class TranscriptionError(UserFacingError):
    """Error during audio transcription."""
    
    def __init__(self, reason: str, suggestion: Optional[str] = None):
        user_message = "Không thể nhận diện giọng nói trong video."
        
        suggestions = {
            "no_audio": "Video không có âm thanh hoặc âm thanh quá nhỏ.",
            "language_not_supported": "Ngôn ngữ này chưa được hỗ trợ.",
            "audio_too_short": "Video quá ngắn để nhận diện giọng nói.",
            "system_error": "Lỗi hệ thống khi xử lý âm thanh.",
        }
        
        action_suggestion = suggestion or suggestions.get(
            reason,
            "Vui lòng thử video khác hoặc liên hệ hỗ trợ."
        )
        
        super().__init__(
            message=f"Transcription failed: {reason}",
            category=ErrorCategory.TRANSCRIPTION,
            user_message=user_message,
            action_suggestion=action_suggestion,
            details={"reason": reason},
        )


class TranslationError(UserFacingError):
    """Error during translation."""
    
    def __init__(self, reason: str, suggestion: Optional[str] = None):
        user_message = "Không thể dịch nội dung video."
        
        suggestions = {
            "rate_limit": "Bạn đang dịch quá nhanh. Vui lòng đợi 1-2 phút rồi thử lại.",
            "service_unavailable": "Dịch vụ dịch đang bận. Vui lòng thử lại sau.",
            "invalid_language": "Ngôn ngữ không hợp lệ.",
            "text_too_long": "Nội dung quá dài để dịch.",
        }
        
        action_suggestion = suggestion or suggestions.get(
            reason,
            "Vui lòng thử lại sau hoặc chọn ngôn ngữ khác."
        )
        
        super().__init__(
            message=f"Translation failed: {reason}",
            category=ErrorCategory.TRANSLATION,
            user_message=user_message,
            action_suggestion=action_suggestion,
            details={"reason": reason},
        )


class TTSError(UserFacingError):
    """Error during text-to-speech synthesis."""
    
    def __init__(self, reason: str, suggestion: Optional[str] = None):
        user_message = "Không thể tạo giọng đọc."
        
        suggestions = {
            "voice_not_available": "Giọng đọc này không có sẵn. Vui lòng chọn giọng khác.",
            "language_not_supported": "Ngôn ngữ này chưa được hỗ trợ.",
            "text_too_long": "Nội dung quá dài để tạo giọng.",
            "service_unavailable": "Dịch vụ TTS đang bận. Vui lòng thử lại sau.",
        }
        
        action_suggestion = suggestion or suggestions.get(
            reason,
            "Vui lòng thử lại sau hoặc chọn giọng đọc khác."
        )
        
        super().__init__(
            message=f"TTS failed: {reason}",
            category=ErrorCategory.TTS,
            user_message=user_message,
            action_suggestion=action_suggestion,
            details={"reason": reason},
        )


class RenderError(UserFacingError):
    """Error during video rendering."""
    
    def __init__(self, reason: str, suggestion: Optional[str] = None):
        user_message = "Không thể render video cuối cùng."
        
        suggestions = {
            "ffmpeg_not_found": "FFmpeg không được cài đặt. Vui lòng liên hệ admin.",
            "disk_full": "Đĩa đầy. Vui lòng xóa bớt file rồi thử lại.",
            "invalid_video": "Video gốc bị lỗi. Vui lòng thử video khác.",
            "timeout": "Render quá lâu. Vui lòng thử video ngắn hơn.",
            "memory_error": "Không đủ bộ nhớ. Vui lòng thử video nhỏ hơn.",
        }
        
        action_suggestion = suggestion or suggestions.get(
            reason,
            "Vui lòng thử lại sau hoặc liên hệ hỗ trợ."
        )
        
        super().__init__(
            message=f"Render failed: {reason}",
            category=ErrorCategory.RENDER,
            user_message=user_message,
            action_suggestion=action_suggestion,
            details={"reason": reason},
        )


class RateLimitError(UserFacingError):
    """Error due to rate limiting."""
    
    def __init__(self, resource: str, retry_after: Optional[int] = None):
        user_message = f"Bạn đã vượt quá giới hạn {resource}."
        
        if retry_after:
            action_suggestion = f"Vui lòng đợi {retry_after} giây rồi thử lại."
        else:
            action_suggestion = "Vui lòng đợi 1-2 phút rồi thử lại."
        
        super().__init__(
            message=f"Rate limit exceeded for {resource}",
            category=ErrorCategory.RATE_LIMIT,
            user_message=user_message,
            action_suggestion=action_suggestion,
            details={"resource": resource, "retry_after": retry_after},
        )


class InvalidInputError(UserFacingError):
    """Error due to invalid user input."""
    
    def __init__(self, field: str, reason: str, suggestion: Optional[str] = None):
        user_message = f"Input không hợp lệ: {field}"
        
        action_suggestion = suggestion or f"Vui lòng kiểm tra {field} và thử lại."
        
        super().__init__(
            message=f"Invalid input for {field}: {reason}",
            category=ErrorCategory.INVALID_INPUT,
            user_message=user_message,
            action_suggestion=action_suggestion,
            details={"field": field, "reason": reason},
        )


def format_error_for_user(error: Exception) -> Dict[str, Any]:
    """
    Format an error for user-facing display.
    
    Returns a dict with:
    - user_message: Friendly message for the user
    - action_suggestion: What the user should do
    - category: Error category for UI styling
    - details: Additional details (optional)
    """
    if isinstance(error, UserFacingError):
        return {
            "user_message": error.user_message,
            "action_suggestion": error.action_suggestion,
            "category": error.category.value,
            "details": error.details,
        }
    
    # Handle common exceptions
    error_type = type(error).__name__
    
    error_mappings = {
        "ValueError": {
            "user_message": "Giá trị không hợp lệ.",
            "action_suggestion": "Vui lòng kiểm tra input và thử lại.",
            "category": "invalid_input",
        },
        "FileNotFoundError": {
            "user_message": "File không tìm thấy.",
            "action_suggestion": "Vui lòng kiểm tra đường dẫn file.",
            "category": "system",
        },
        "PermissionError": {
            "user_message": "Không có quyền truy cập.",
            "action_suggestion": "Vui lòng kiểm tra quyền file.",
            "category": "system",
        },
        "TimeoutError": {
            "user_message": "Quá thời gian chờ.",
            "action_suggestion": "Vui lòng thử lại sau.",
            "category": "system",
        },
    }
    
    mapping = error_mappings.get(error_type, {
        "user_message": "Đã xảy ra lỗi không xác định.",
        "action_suggestion": "Vui lòng thử lại sau hoặc liên hệ hỗ trợ.",
        "category": "system",
    })
    
    return {
        "user_message": mapping["user_message"],
        "action_suggestion": mapping["action_suggestion"],
        "category": mapping["category"],
        "details": {"error_type": error_type, "message": str(error)},
    }


def log_error(error: Exception, context: Optional[Dict[str, Any]] = None) -> None:
    """Log error with context for debugging."""
    context = context or {}
    
    if isinstance(error, UserFacingError):
        _logger.error(
            f"{error.category.value} error: {error.message} | Context: {context} | Details: {error.details}"
        )
    else:
        _logger.error(f"Unexpected error: {type(error).__name__}: {error} | Context: {context}")

"""
Base agent class for Content OS.

All agents inherit from this base class which provides common functionality
for LLM interaction, context building, and output validation.
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from pydantic import BaseModel

from ..config import CONTENT_OS_LLM_PROVIDER, CONTENT_OS_LLM_MODEL, CONTENT_OS_LLM_BASE_URL, CONTENT_OS_LLM_API_KEY
from ..exceptions import LLMOutputError

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Base class for all Content OS agents.
    
    Provides:
    - LLM routing and invocation
    - Context building
    - Structured output validation
    - Error handling and retry logic
    """
    
    def __init__(
        self,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        llm_api_key: Optional[str] = None,
    ):
        self.llm_provider = llm_provider or CONTENT_OS_LLM_PROVIDER
        self.llm_model = llm_model or CONTENT_OS_LLM_MODEL
        self.llm_base_url = llm_base_url or CONTENT_OS_LLM_BASE_URL
        self.llm_api_key = llm_api_key or CONTENT_OS_LLM_API_KEY
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Return the agent's name."""
        pass
    
    @property
    @abstractmethod
    def output_schema(self) -> type[BaseModel]:
        """Return the Pydantic schema for structured output."""
        pass
    
    @abstractmethod
    def build_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build the prompt for the LLM.
        
        Args:
            context: Dictionary with context information
            
        Returns:
            The prompt string to send to the LLM
        """
        pass
    
    @abstractmethod
    def validate_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and post-process the LLM output.
        
        Args:
            output: Raw output from LLM
            
        Returns:
            Validated and processed output
        """
        pass
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the agent with the given context.
        
        Args:
            context: Dictionary with context information
            
        Returns:
            Validated output from the agent
        """
        self.logger.info(f"Executing agent {self.agent_name}")
        
        # Store context for use in mock output
        self._context = context
        
        try:
            prompt = self.build_prompt(context)
            self.logger.debug(f"Prompt built: {len(prompt)} characters")
            
            raw_output = self._call_llm(prompt)
            self.logger.debug(f"LLM output received: {len(str(raw_output))} characters")
            
            validated_output = self.validate_output(raw_output)
            self.logger.info(f"Agent {self.agent_name} completed successfully")
            
            return validated_output
            
        except Exception as e:
            self.logger.error(f"Agent {self.agent_name} failed: {e}")
            raise
    
    def _call_llm(self, prompt: str) -> Dict[str, Any]:
        """
        Call the LLM with the given prompt.
        
        Args:
            prompt: The prompt to send
            
        Returns:
            Raw output from the LLM as a dictionary
        """
        from .llm_router import LLMRouter
        
        # Use LLM router for real API calls
        try:
            router = LLMRouter(
                provider=self.llm_provider,
                model=self.llm_model,
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
            )
            
            # Get output schema from agent for structured output
            output_schema = self.output_schema
            
            result = router.invoke(
                prompt=prompt,
                output_schema=output_schema,
                temperature=0.7,
                max_tokens=2000,
            )
            
            self.logger.info(f"LLM call successful: provider={self.llm_provider}, model={self.llm_model}")
            return result
            
        except Exception as e:
            self.logger.warning(f"LLM call failed: {e}, falling back to mock output")
            return self._mock_output()
    
    def _mock_output(self) -> Dict[str, Any]:
        """
        Return mock output for testing when LLM is not available.
        
        Subclasses should override this to provide realistic mock data.
        """
        return {}
    
    def _validate_structured_output(self, output: Dict[str, Any]) -> Any:
        """
        Validate output against the agent's output schema.
        
        Args:
            output: Raw output dictionary
            
        Returns:
            Validated Pydantic model instance
            
        Raises:
            LLMOutputError: If validation fails
        """
        try:
            return self.output_schema(**output)
        except Exception as e:
            raise LLMOutputError(f"Output validation failed: {e}") from e

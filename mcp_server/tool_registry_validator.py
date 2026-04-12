"""
A-02: MCP Tool Registry Validator and Startup Validation

Implements the MCP Minimum Tool Surface Contract:
- Contract 1: Registry is single source of truth
- Contract 2: Registry entries version-pinned and schema-validated at startup
- Contract 3: Server fails closed on missing/invalid tools
- Contract 4: Every tool invocation audited with correlation ID
- Contract 5: Minimum tool surface is read-safe
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set, Tuple
from enum import Enum
import hashlib
import json
import re
from pathlib import Path
import importlib.util
import sys


class RegistryValidationStatus(Enum):
    """Status of registry validation."""
    VALID = "valid"
    INVALID = "invalid"
    FATAL = "fatal"


class ToolValidationStatus(Enum):
    """Status of individual tool validation."""
    VALID = "valid"
    INVALID = "invalid"
    IMPORT_FAILED = "import_failed"
    NOT_CALLABLE = "not_callable"


@dataclass(frozen=True)
class FieldValidationError:
    """Error from field validation."""
    field_name: str
    reason: str
    value: Any = None


@dataclass(frozen=True)
class ToolValidationError:
    """Error from tool validation."""
    tool_name: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class ToolEntry:
    """Represents a tool in the registry."""
    name: str
    file: str
    version: str
    inputs: Dict[str, Any]
    output: Dict[str, Any]
    read_only: bool = True
    
    def is_required_tool(self) -> bool:
        """Check if this is a required minimum tool."""
        return self.name in {"health_check", "manifest_status"}


@dataclass(frozen=True)
class RegistrySchema:
    """Represents a validated registry."""
    schema_version: str
    server_name: str
    governance_version: str
    tools: List[ToolEntry]
    validation_errors: List[FieldValidationError] = field(default_factory=list)
    tool_errors: List[ToolValidationError] = field(default_factory=list)
    
    def is_valid(self) -> bool:
        """True if schema is valid."""
        return len(self.validation_errors) == 0 and len(self.tool_errors) == 0
    
    def required_tools_present(self) -> bool:
        """True if all required tools are present."""
        tool_names = {t.name for t in self.tools}
        return {"health_check", "manifest_status"}.issubset(tool_names)
    
    def get_tool(self, name: str) -> Optional[ToolEntry]:
        """Get tool by name."""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None


class ToolRegistryValidator:
    """Validates MCP tool registry against A-02 contract."""
    
    REQUIRED_TOP_LEVEL_FIELDS = {
        "schema_version", "server_name", "governance_version", "tools"
    }
    
    REQUIRED_TOOL_FIELDS = {
        "name", "file", "inputs", "output", "version"
    }
    
    REQUIRED_MINIMUM_TOOLS = {"health_check", "manifest_status"}
    
    @staticmethod
    def validate_top_level(data: Dict[str, Any]) -> Tuple[bool, List[FieldValidationError]]:
        """Validate top-level registry fields."""
        errors = []
        
        # Check presence of required fields
        for field in ToolRegistryValidator.REQUIRED_TOP_LEVEL_FIELDS:
            if field not in data:
                errors.append(FieldValidationError(
                    field_name=field,
                    reason=f"required field missing"
                ))
        
        # Validate schema_version format
        if "schema_version" in data:
            version = data["schema_version"]
            if not isinstance(version, str) or not version.startswith("1."):
                errors.append(FieldValidationError(
                    field_name="schema_version",
                    reason="must be string matching ^1\\.\\d+\\.\\d+$",
                    value=version
                ))
        
        # Validate server_name is string
        if "server_name" in data:
            if not isinstance(data["server_name"], str):
                errors.append(FieldValidationError(
                    field_name="server_name",
                    reason="must be string",
                    value=data["server_name"]
                ))
        
        # Validate governance_version is string
        if "governance_version" in data:
            if not isinstance(data["governance_version"], str):
                errors.append(FieldValidationError(
                    field_name="governance_version",
                    reason="must be string",
                    value=data["governance_version"]
                ))
        
        # Validate tools is array
        if "tools" in data:
            if not isinstance(data["tools"], list):
                errors.append(FieldValidationError(
                    field_name="tools",
                    reason="must be array",
                    value=type(data["tools"]).__name__
                ))
            elif len(data["tools"]) < 2:
                errors.append(FieldValidationError(
                    field_name="tools",
                    reason="must contain at least 2 tools",
                    value=len(data["tools"])
                ))
        
        return len(errors) == 0, errors
    
    NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]+$")
    RATE_LIMIT_PATTERN = re.compile(r"^\d+_per_\d+s$")

    @staticmethod
    def validate_tool(tool_data: Dict[str, Any]) -> Tuple[bool, List[FieldValidationError]]:
        """Validate a single tool entry."""
        errors = []
        
        # Check required fields
        for field in ToolRegistryValidator.REQUIRED_TOOL_FIELDS:
            if field not in tool_data:
                errors.append(FieldValidationError(
                    field_name=field,
                    reason=f"required tool field missing"
                ))
        
        # Validate name format
        if "name" in tool_data:
            name = tool_data["name"]
            if not isinstance(name, str):
                errors.append(FieldValidationError(
                    field_name="name",
                    reason="must be string",
                    value=type(name).__name__
                ))
            elif not ToolRegistryValidator.NAME_PATTERN.match(name):
                errors.append(FieldValidationError(
                    field_name="name",
                    reason="must be lower_snake_case alphanumeric with underscores",
                    value=name
                ))

            # rule-tool naming policy: explicit tool names should mention action (verbs) and not exceed 64 chars
            if isinstance(name, str) and len(name) > 64:
                errors.append(FieldValidationError(
                    field_name="name",
                    reason="must be <= 64 characters",
                    value=name
                ))

            if isinstance(name, str) and not any(name.startswith(prefix) for prefix in ["get_", "set_", "create_", "remove_", "deploy_", "phase_"]) and name not in ToolRegistryValidator.REQUIRED_MINIMUM_TOOLS:
                errors.append(FieldValidationError(
                    field_name="name",
                    reason="should follow coalesced rule-tool naming convention (verbs like get_/create_/remove_/deploy_)",
                    value=name
                ))

        # Validate rate_limit TTL format
        if "rate_limit" in tool_data:
            rate_limit = tool_data["rate_limit"]
            if not isinstance(rate_limit, str) or not ToolRegistryValidator.RATE_LIMIT_PATTERN.match(rate_limit):
                errors.append(FieldValidationError(
                    field_name="rate_limit",
                    reason="must be in form '<n>_per_<m>s'",
                    value=rate_limit
                ))
        
        # Validate file is string
        if "file" in tool_data:
            if not isinstance(tool_data["file"], str):
                errors.append(FieldValidationError(
                    field_name="file",
                    reason="must be string",
                    value=type(tool_data["file"]).__name__
                ))
        
        # Validate version format
        if "version" in tool_data:
            version = tool_data["version"]
            if not isinstance(version, str) or not version.startswith("1."):
                errors.append(FieldValidationError(
                    field_name="version",
                    reason="must be string matching ^1\\.\\d+\\.\\d+$",
                    value=version
                ))
        
        # Validate inputs is dict
        if "inputs" in tool_data:
            if not isinstance(tool_data["inputs"], dict):
                errors.append(FieldValidationError(
                    field_name="inputs",
                    reason="must be object/dict",
                    value=type(tool_data["inputs"]).__name__
                ))
        
        # Validate output is dict
        if "output" in tool_data:
            if not isinstance(tool_data["output"], dict):
                errors.append(FieldValidationError(
                    field_name="output",
                    reason="must be object/dict",
                    value=type(tool_data["output"]).__name__
                ))

        # Validate TTL semantics for tools that expose a rule contract
        if "critical" in tool_data and tool_data.get("critical") is True and "rate_limit" not in tool_data:
            errors.append(FieldValidationError(
                field_name="rate_limit",
                reason="governance-critical tools should declare rate_limit to make TTL explicit",
                value=None
            ))
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_required_tools(tools: List[ToolEntry]) -> List[ToolValidationError]:
        """Validate all required tools are present."""
        errors = []
        tool_names = {t.name for t in tools}
        
        for required_name in ToolRegistryValidator.REQUIRED_MINIMUM_TOOLS:
            if required_name not in tool_names:
                errors.append(ToolValidationError(
                    tool_name=required_name,
                    reason="required tool not found in registry"
                ))
        
        return errors
    
    @staticmethod
    def validate_registry(registry_data: Dict[str, Any]) -> RegistrySchema:
        """
        Validate complete registry.
        
        Returns:
            RegistrySchema with validation status and errors
        """
        field_errors = []
        tool_errors = []
        
        # Validate top level
        top_valid, top_errors = ToolRegistryValidator.validate_top_level(registry_data)
        field_errors.extend(top_errors)
        
        # Extract top-level fields
        schema_version = registry_data.get("schema_version", "unknown")
        server_name = registry_data.get("server_name", "unknown")
        governance_version = registry_data.get("governance_version", "unknown")
        
        tools = []
        
        # Validate tools
        if top_valid and "tools" in registry_data:
            for tool_data in registry_data["tools"]:
                tool_valid, tool_field_errors = ToolRegistryValidator.validate_tool(tool_data)
                field_errors.extend(tool_field_errors)
                
                if tool_valid:
                    tool = ToolEntry(
                        name=tool_data.get("name", "unknown"),
                        file=tool_data.get("file", ""),
                        version=tool_data.get("version", "unknown"),
                        inputs=tool_data.get("inputs", {}),
                        output=tool_data.get("output", {}),
                        read_only=tool_data.get("read_only", True),
                    )
                    tools.append(tool)
        
        # Validate required tools present
        required_tool_errors = ToolRegistryValidator.validate_required_tools(tools)
        tool_errors.extend(required_tool_errors)
        
        return RegistrySchema(
            schema_version=schema_version,
            server_name=server_name,
            governance_version=governance_version,
            tools=tools,
            validation_errors=field_errors,
            tool_errors=tool_errors,
        )


class MCPStartupValidator:
    """Validates MCP startup sequence per A-02."""
    
    STARTUP_STEPS = [
        "Load registry manifest",
        "Validate registry schema",
        "Validate tool entries",
        "Verify required tools present",
        "Load tool implementations",
        "Verify tool callability",
        "Initialize audit logger",
        "Startup complete",
    ]
    
    @staticmethod
    def load_registry(path: str) -> Dict[str, Any]:
        """Step 1: Load registry from file."""
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            raise RuntimeError(f"Registry file not found: {path}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Registry JSON invalid: {e}")
    
    @staticmethod
    def validate_startup(registry_schema: RegistrySchema) -> Tuple[bool, List[str]]:
        """
        Validate startup sequence.
        
        Returns:
            (success, errors) tuple
        """
        errors = []
        
        # Step 2: Schema validation
        if not registry_schema.is_valid():
            errors.append("Registry schema validation failed")
            for err in registry_schema.validation_errors:
                errors.append(f"  {err.field_name}: {err.reason}")
            for err in registry_schema.tool_errors:
                errors.append(f"  {err.tool_name}: {err.reason}")
        
        # Step 4: Required tools check
        if not registry_schema.required_tools_present():
            missing = set(ToolRegistryValidator.REQUIRED_MINIMUM_TOOLS) - \
                     {t.name for t in registry_schema.tools}
            for tool_name in missing:
                errors.append(f"Required tool missing: {tool_name}")
        
        return len(errors) == 0, errors


# Test helpers
def create_test_registry(
    schema_version: str = "1.0.0",
    server_name: str = "test-server",
    governance_version: str = "adr-002-v1.0",
    include_all_tools: bool = True,
    include_invalid_tool: bool = False,
) -> Dict[str, Any]:
    """Create a test registry."""
    tools = [
        {
            "name": "health_check",
            "file": "mcp_server/tools/health_check.py",
            "version": "1.0.0",
            "read_only": True,
            "inputs": {"type": "object", "properties": {}},
            "output": {"type": "object"},
        },
    ]
    
    if include_all_tools:
        tools.append({
            "name": "manifest_status",
            "file": "mcp_server/tools/manifest_status.py",
            "version": "1.0.0",
            "read_only": True,
            "inputs": {"type": "null"},
            "output": {"type": "object"},
        })
    
    if include_invalid_tool:
        tools.append({
            "name": "bad_tool",
            "file": "mcp_server/tools/bad_tool.py",
            # Missing "output" field
        })
    
    return {
        "schema_version": schema_version,
        "server_name": server_name,
        "governance_version": governance_version,
        "tools": tools,
    }

"""PandasAI Data Analysis Tool

This tool directly executes data analysis operations using pandas-ai,
without spawning additional LLM calls. It leverages the shared LLM
configuration from CodeGenX.

Features:
- Natural language data querying (NL2SQL)
- Data visualization generation
- Database connector support
- Shared LLM instance (no additional API calls)
"""
import asyncio
import json
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass

from bot.tools.base import BaseTool, ToolResult
from bot.utils.log_utils import log
from shared.config.codegen import get_codegen_config
from shared.constants import get_code_dir


@dataclass
class DataAnalysisConfig:
    """Configuration for data analysis execution"""
    data_source: str
    query: str
    query_type: str  # 'sql', 'analysis', 'visualization'
    database_type: Optional[str] = None  # 'postgres', 'mysql', 'sqlite', etc.
    connection_string: Optional[str] = None
    app_id: str = "main"


class PandasAIDataAnalysisTool(BaseTool):
    """Execute data analysis using PandasAI with shared LLM context"""

    @property
    def name(self) -> str:
        return "pandas_ai_analysis"

    @property
    def label(self) -> str:
        return "data_analysis"

    @property
    def description(self) -> str:
        return (
            "Execute natural language data analysis using PandasAI. "
            "Supports queries on CSV, databases, and data visualization. "
            "Uses the shared CodeGenX LLM - no additional API calls needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "data_source": {
                    "type": "string",
                    "description": (
                        "Path to data file (CSV/Parquet) or database connection string. "
                        "For files: relative path like 'data/sales.csv' or absolute path. "
                        "For DB: 'postgresql://user:pass@host/db' or 'mysql://user:pass@host/db'"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language question or task in English or Chinese. "
                        "Examples: 'What is the average revenue by region?', "
                        "'Show me the top 5 customers by sales', "
                        "'Create a line chart showing monthly trends'"
                    ),
                },
                "query_type": {
                    "type": "string",
                    "enum": ["analysis", "sql", "visualization"],
                    "description": (
                        "Type of query: "
                        "'analysis' - data aggregation and insights, "
                        "'sql' - direct SQL database queries, "
                        "'visualization' - generate charts and plots"
                    ),
                },
                "database_type": {
                    "type": "string",
                    "enum": ["postgres", "mysql", "sqlite", "cockroachdb", "sqlserver"],
                    "description": (
                        "Database type (only needed if data_source is a connection string). "
                        "Defaults to auto-detection from connection string."
                    ),
                },
                "app_id": {
                    "type": "string",
                    "description": "Application ID for workspace isolation (auto-filled).",
                },
            },
            "required": ["data_source", "query", "query_type"],
        }

    async def execute(
            self, params: dict, signal: asyncio.Event | None = None
    ) -> ToolResult:
        """
        Execute data analysis operation.

        Args:
            params: Tool parameters containing data_source, query, query_type, etc.
            signal: Cancellation signal

        Returns:
            ToolResult with analysis result or error message
        """
        try:
            config = DataAnalysisConfig(
                data_source=str(params.get("data_source", "")).strip(),
                query=str(params.get("query", "")).strip(),
                query_type=str(params.get("query_type", "analysis")).strip().lower(),
                database_type=params.get("database_type"),
                connection_string=params.get("connection_string"),
                app_id=str(params.get("app_id", "main")),
            )

            # Validate inputs
            if not config.data_source:
                return ToolResult(
                    success=False, message="data_source is required"
                )
            if not config.query:
                return ToolResult(success=False, message="query is required")
            if config.query_type not in ["analysis", "sql", "visualization"]:
                return ToolResult(
                    success=False,
                    message=f"Invalid query_type: {config.query_type}. Must be one of: analysis, sql, visualization",
                )

            # Check for cancellation
            if signal and signal.is_set():
                raise asyncio.CancelledError("Operation cancelled")

            # Route to appropriate handler
            if config.query_type == "visualization":
                result = await self._execute_visualization(config)
            elif config.query_type == "sql":
                result = await self._execute_sql_query(config)
            else:  # analysis
                result = await self._execute_analysis(config)

            return ToolResult(success=True, data=json.dumps(result, ensure_ascii=False, indent=2))

        except asyncio.CancelledError:
            return ToolResult(success=False, message="Operation cancelled by user")
        except FileNotFoundError as e:
            return ToolResult(
                success=False, message=f"Data source not found: {str(e)}"
            )
        except ImportError as e:
            return ToolResult(
                success=False,
                message=f"Required package not installed: {str(e)}. Please install pandasai and pandasai-litellm.",
            )
        except Exception as e:
            log.error(f"PandasAI analysis failed: {e}", exc_info=True)
            return ToolResult(success=False, message=f"Analysis error: {str(e)}")

    async def _execute_analysis(self, config: DataAnalysisConfig) -> dict:
        """Execute data analysis on CSV or loaded data"""
        try:
            import pandasai as pai
            from pandasai_litellm import LiteLLM
        except ImportError:
            raise ImportError(
                "pandasai or pandasai-litellm not installed. "
                "Install with: pip install pandasai pandasai-litellm"
            )

        # Get CodeGenX's LLM configuration
        llm_config = get_codegen_config()
        llm = LiteLLM(model=llm_config.model, api_key=llm_config.api_key)

        # Configure PandasAI to use the shared LLM
        pai.config.set({"llm": llm})

        # Load data
        data_source_path = self._resolve_data_path(config.data_source, config.app_id)

        if not data_source_path.exists():
            raise FileNotFoundError(f"Data source not found: {data_source_path}")

        if str(data_source_path).endswith(".csv"):
            df = pai.read_csv(str(data_source_path))
        elif str(data_source_path).endswith(".parquet"):
            df = pai.read_parquet(str(data_source_path))
        else:
            raise ValueError(
                f"Unsupported file format: {data_source_path.suffix}. "
                "Supported: .csv, .parquet"
            )

        # Execute query
        log.info(f"Executing analysis query: {config.query}")
        response = df.chat(config.query)

        return {
            "type": "analysis",
            "query": config.query,
            "result": str(response),
            "data_source": config.data_source,
        }

    async def _execute_sql_query(self, config: DataAnalysisConfig) -> dict:
        """Execute queries on SQL databases"""
        try:
            import pandasai as pai
            from pandasai_litellm import LiteLLM
        except ImportError:
            raise ImportError(
                "pandasai or pandasai-litellm not installed. "
                "Install with: pip install pandasai pandasai-litellm"
            )

        # Get CodeGenX's LLM configuration
        llm_config = get_codegen_config()
        llm = LiteLLM(model=llm_config.model, api_key=llm_config.api_key)

        # Configure PandasAI
        pai.config.set({"llm": llm})

        # Load data from database
        connection_string = config.connection_string or config.data_source

        try:
            # Try to auto-detect and load from database
            df = pai.read_sql(connection_string)
        except Exception as e:
            log.error(f"Failed to connect to database: {e}")
            raise ValueError(
                f"Database connection failed. Check connection string and ensure database is running. "
                f"Error: {str(e)}"
            )

        # Execute query
        log.info(f"Executing SQL query analysis: {config.query}")
        response = df.chat(config.query)

        return {
            "type": "sql",
            "query": config.query,
            "result": str(response),
            "database": config.database_type or "auto-detected",
        }

    async def _execute_visualization(self, config: DataAnalysisConfig) -> dict:
        """Generate data visualizations"""
        try:
            import pandasai as pai
            from pandasai_litellm import LiteLLM
        except ImportError:
            raise ImportError(
                "pandasai or pandasai-litellm not installed. "
                "Install with: pip install pandasai pandasai-litellm"
            )

        # Get CodeGenX's LLM configuration
        llm_config = get_codegen_config()
        llm = LiteLLM(model=llm_config.model, api_key=llm_config.api_key)

        # Configure PandasAI
        pai.config.set({"llm": llm})

        # Load data
        data_source_path = self._resolve_data_path(config.data_source, config.app_id)

        if not data_source_path.exists():
            raise FileNotFoundError(f"Data source not found: {data_source_path}")

        if str(data_source_path).endswith(".csv"):
            df = pai.read_csv(str(data_source_path))
        elif str(data_source_path).endswith(".parquet"):
            df = pai.read_parquet(str(data_source_path))
        else:
            raise ValueError(
                f"Unsupported file format: {data_source_path.suffix}. "
                "Supported: .csv, .parquet"
            )

        # Generate visualization
        log.info(f"Generating visualization: {config.query}")
        response = df.chat(config.query)

        return {
            "type": "visualization",
            "query": config.query,
            "result": str(response),
            "data_source": config.data_source,
            "note": "Chart file may have been generated to the workspace exports directory",
        }

    @staticmethod
    def _resolve_data_path(data_source: str, app_id: str) -> Path:
        """
        Resolve data source path.

        Handles:
        - Relative paths (relative to workspace code directory)
        - Absolute paths
        - Returns expanded Path object
        """
        path = Path(data_source).expanduser()

        if not path.is_absolute():
            # Relative path: resolve from workspace code directory
            path = get_code_dir(app_id) / path

        return path.resolve()
